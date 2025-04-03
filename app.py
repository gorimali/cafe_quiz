import os
import json # JSON işlemek için
import requests # Facebook API istekleri için
from flask import Flask, render_template, request, redirect, url_for, session, flash # Flash ekledik
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit # SocketIO ekledik
import logging
import time # Zamanlama için
import threading # Arka plan görevi için
import random # Rastgele soru seçimi için (opsiyonel)
from datetime import datetime, timedelta # Zamanlama için

# --- Uygulama ve Yapılandırma ---
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
# async_mode=None, gevent veya eventlet kurulu değilse varsayılanı kullanır
socketio = SocketIO(app, async_mode=None) # SocketIO'yu başlat

# Ortam Değişkenleri (Render'da ayarlanacak)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'yerel_cok_gizli_anahtar_degistir')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///local_quiz.db').replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
FACEBOOK_APP_ID = os.environ.get('FACEBOOK_APP_ID')
FACEBOOK_APP_SECRET = os.environ.get('FACEBOOK_APP_SECRET')

# Facebook Login için gerekli URL'ler (Callback URL'si Render'da ayarladığınızla aynı olmalı)
# Yerel test için farklı bir callback kullanabilirsiniz ama Render'da doğru olanı ayarlayın.
# Ortam değişkeni olarak ayarlamak daha esnek olabilir:
# FACEBOOK_REDIRECT_URI = os.environ.get('FACEBOOK_REDIRECT_URI', 'http://127.0.0.1:5000/facebook/callback')
# Şimdilik Render URL'sini varsayalım (kendi adresinizle değiştirin!):
FACEBOOK_REDIRECT_URI = 'https://cafe-quiz.onrender.com/facebook/callback' # <<< KENDİ URL'NİZLE DEĞİŞTİRİN!
FACEBOOK_API_VERSION = 'v18.0' # API sürümünü belirtmek iyi practice'dir

db = SQLAlchemy(app)

# --- Global Quiz State ---
current_question_data = {
    "question": None,
    "end_time": None,
    "question_id": None # Cevapları doğrulamak için
}
quiz_timer_thread = None
stop_event = threading.Event() # Arka plan görevini durdurmak için
QUESTION_DURATION = 15 # Saniye cinsinden soru süresi

# --- Veritabanı Modelleri ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    facebook_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    # İleride skorları buraya bağlayabiliriz
    # scores = db.relationship('Score', backref='user', lazy=True)

    def __repr__(self):
        return f'<User {self.name} (FB ID: {self.facebook_id})>'

class Question(db.Model):
    # ... (Önceki Question modeli aynı kalıyor) ...
    id = db.Column(db.Integer, primary_key=True)
    question_text = db.Column(db.String(500), nullable=False)
    option1 = db.Column(db.String(100), nullable=False)
    option2 = db.Column(db.String(100), nullable=False)
    option3 = db.Column(db.String(100), nullable=False)
    option4 = db.Column(db.String(100), nullable=False)
    correct_answer = db.Column(db.String(100), nullable=False)

    def get_options(self):
        return [self.option1, self.option2, self.option3, self.option4]

    def to_dict(self):
        """Soruyu istemciye göndermek için sözlük formatına çevirir."""
        return {
            'id': self.id,
            'question_text': self.question_text,
            'options': self.get_options(),
            # Doğru cevabı istemciye göndermiyoruz!
        }

# --- Yardımcı Fonksiyon ---
def get_current_user():
    """Session'daki user_id'ye göre User nesnesini döndürür."""
    user_id = session.get('user_id')
    if user_id:
        return User.query.get(user_id)
    return None

# --- Rotalar (Routes) ---

@app.route('/')
def index():
    user = get_current_user()
    if not user:
        # Kullanıcı giriş yapmamışsa, giriş sayfasına yönlendir (veya giriş butonu göster)
        return render_template('login.html') # Yeni bir login şablonu oluşturacağız

    # Kullanıcı giriş yapmışsa quiz sayfasını render et.
    # Gerçek soru yüklemesi client-side JS ve SocketIO ile yapılacak.
    logging.info(f"Rendering quiz page for user {user.name}")
    return render_template('quiz.html', current_user=user)
    # Not: Skor gibi bilgiler artık client-side'da veya başka bir mekanizma ile tutulabilir.
    # Şimdilik basit tutuyoruz.

# Yeni giriş sayfası rotası
@app.route('/login')
def login_page():
    # Zaten giriş yapmışsa ana sayfaya yönlendir
    if get_current_user():
        return redirect(url_for('index'))
    return render_template('login.html')

# Facebook Login Başlatma
@app.route('/login/facebook')
def facebook_login():
    if not FACEBOOK_APP_ID:
        flash("Facebook Login is not configured correctly.", "danger")
        return redirect(url_for('login_page'))

    # Facebook yetkilendirme URL'si oluştur
    fb_auth_url = (
        f"https://www.facebook.com/{FACEBOOK_API_VERSION}/dialog/oauth?"
        f"client_id={FACEBOOK_APP_ID}"
        f"&redirect_uri={FACEBOOK_REDIRECT_URI}"
        f"&scope=public_profile,email" # İstediğimiz izinler (email opsiyonel)
        # CSRF koruması için 'state' parametresi eklemek iyi practice'dir.
        # state = secrets.token_urlsafe(16)
        # session['oauth_state'] = state
        # f"&state={state}"
    )
    logging.info("Redirecting to Facebook for authorization.")
    return redirect(fb_auth_url)

# Facebook Callback İşleme
@app.route('/facebook/callback')
def facebook_callback():
    # Hata kontrolü
    error = request.args.get('error')
    if error:
        error_desc = request.args.get('error_description', 'Unknown Facebook error.')
        logging.error(f"Facebook login error: {error} - {error_desc}")
        flash(f"Facebook login failed: {error_desc}", "danger")
        return redirect(url_for('login_page'))

    # CSRF kontrolü (eğer state kullanıldıysa)
    # received_state = request.args.get('state')
    # expected_state = session.pop('oauth_state', None)
    # if not received_state or received_state != expected_state:
    #     flash("Invalid state parameter. Possible CSRF attack.", "danger")
    #     return redirect(url_for('login_page'))

    code = request.args.get('code')
    if not code:
        flash("Facebook did not return an authorization code.", "danger")
        return redirect(url_for('login_page'))

    logging.info("Received authorization code from Facebook. Exchanging for access token.")

    # --- Kod ile Access Token Al ---
    token_url = f"https://graph.facebook.com/{FACEBOOK_API_VERSION}/oauth/access_token"
    token_params = {
        'client_id': FACEBOOK_APP_ID,
        'redirect_uri': FACEBOOK_REDIRECT_URI,
        'client_secret': FACEBOOK_APP_SECRET,
        'code': code
    }
    try:
        token_response = requests.get(token_url, params=token_params)
        token_response.raise_for_status() # HTTP hatası varsa exception fırlat
        token_data = token_response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error requesting access token: {e}")
        logging.error(f"Response content: {token_response.content}")
        flash("Could not connect to Facebook to get access token.", "danger")
        return redirect(url_for('login_page'))
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON from access token response: {token_response.text}")
        flash("Received invalid response from Facebook for access token.", "danger")
        return redirect(url_for('login_page'))


    access_token = token_data.get('access_token')
    if not access_token:
        error_details = token_data.get('error', {})
        logging.error(f"Facebook did not return access token. Error: {error_details}")
        flash(f"Failed to get access token from Facebook: {error_details.get('message', 'Unknown error')}", "danger")
        return redirect(url_for('login_page'))

    logging.info("Successfully obtained access token. Fetching user profile.")

    # --- Access Token ile Kullanıcı Bilgilerini Al ---
    user_info_url = f"https://graph.facebook.com/{FACEBOOK_API_VERSION}/me"
    user_info_params = {
        'fields': 'id,name,picture', # İstediğimiz alanlar (picture varsayılan küçük boy döner)
        'access_token': access_token
    }
    # Daha büyük profil resmi için: 'fields': 'id,name,picture.type(large)'
    try:
        user_info_response = requests.get(user_info_url, params=user_info_params)
        user_info_response.raise_for_status()
        user_data = user_info_response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error requesting user info: {e}")
        logging.error(f"Response content: {user_info_response.content}")
        flash("Could not connect to Facebook to get user profile.", "danger")
        return redirect(url_for('login_page'))
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON from user info response: {user_info_response.text}")
        flash("Received invalid response from Facebook for user profile.", "danger")
        return redirect(url_for('login_page'))


    facebook_id = user_data.get('id')
    user_name = user_data.get('name')
    # profile_pic_data = user_data.get('picture', {}).get('data', {}).get('url') # Picture URL'si

    if not facebook_id or not user_name:
        logging.error(f"Facebook response missing ID or Name: {user_data}")
        flash("Could not retrieve necessary user information from Facebook.", "danger")
        return redirect(url_for('login_page'))

    logging.info(f"Fetched user info: ID={facebook_id}, Name={user_name}")

    # --- Kullanıcıyı Veritabanında Bul veya Oluştur ---
    try:
        user = User.query.filter_by(facebook_id=facebook_id).first()
        if user:
            logging.info(f"Existing user found: {user.name}")
            # İsteğe bağlı: İsim değişmişse güncelle
            if user.name != user_name:
                user.name = user_name
                db.session.commit()
        else:
            logging.info(f"Creating new user: {user_name}")
            user = User(facebook_id=facebook_id, name=user_name)
            db.session.add(user)
            db.session.commit()
            logging.info(f"New user created with ID: {user.id}")

        # Kullanıcıyı session'a kaydet
        session['user_id'] = user.id
        session['user_name'] = user.name # Kolay erişim için
        # session['profile_pic'] = profile_pic_data # İsterseniz bunu da saklayın

        logging.info(f"User {user.name} logged in successfully.")
        flash(f"Welcome, {user.name}!", "success")
        return redirect(url_for('index')) # Quiz sayfasına yönlendir

    except Exception as e:
        db.session.rollback() # Veritabanı hatası olursa geri al
        logging.exception("Database error during user lookup/creation:")
        flash("An error occurred while accessing user data. Please try again.", "danger")
        return redirect(url_for('login_page'))


@app.route('/logout')
def logout():
    logging.info(f"User {session.get('user_name', 'Unknown')} logging out.")
    # Session'dan kullanıcı bilgilerini temizle
    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('profile_pic', None)
    # Quiz ile ilgili session verilerini de temizlemek iyi olabilir
    session.pop('current_question_index', None)
    session.pop('score', None)
    session.pop('quiz_over', None)
    session.pop('total_questions', None)
    flash("You have been logged out.", "info")
    return redirect(url_for('login_page')) # Giriş sayfasına yönlendir


# Cevap gönderme rotasını da giriş kontrolü ile güncelle
@app.route('/submit_answer', methods=['POST'])
def submit_answer():
    user = get_current_user()
    if not user:
        flash("Please log in to submit answers.", "warning")
        return redirect(url_for('login_page'))

    # Cevabı global aktif soruya göre işle
    try:
        user_answer = request.form.get('answer')
        submitted_question_id = request.form.get('question_id') # Hangi soruya cevap verildiğini bilmek için

        if not user_answer:
            flash("Please select an answer.", "warning")
            return redirect(url_for('index'))

        # Global state'deki soru ile karşılaştır
        global current_question_data
        active_question_id = current_question_data.get("question_id")
        active_question = current_question_data.get("question") # Question nesnesi

        # Zamanında mı cevapladı ve doğru soruya mı?
        if not active_question_id or str(submitted_question_id) != str(active_question_id):
            flash("Too late, or answer submitted for a previous question!", "info")
            return redirect(url_for('index'))

        # Zaman kontrolü (opsiyonel ama iyi fikir)
        # if datetime.now() > current_question_data.get("end_time"):
        #     flash("Time is up for this question!", "info")
        #     return redirect(url_for('index'))

        # Veritabanından doğru cevabı al (global state'de tutmak yerine)
        # Bu, state'i daha hafif tutar.
        correct_q_from_db = Question.query.get(active_question_id)
        if not correct_q_from_db:
             flash("Could not verify the answer for the current question.", "danger")
             return redirect(url_for('index'))

        is_correct = (user_answer == correct_q_from_db.correct_answer)

        logging.info(f"User {user.name} submitted '{user_answer}' for Q_ID {active_question_id}. Correct: {is_correct}")

        # Skorlama mantığı buraya eklenebilir (örneğin ayrı bir Score tablosu ile)
        if is_correct:
            # TODO: Kullanıcının skorunu güncelle (veritabanında?)
            flash("Correct!", "success")
            pass
        else:
            flash(f"Incorrect. The correct answer was: {correct_q_from_db.correct_answer}", "danger")
            pass

        # Cevap gönderildikten sonra ana sayfaya yönlendir.
        # Kullanıcı yeni soruyu SocketIO üzerinden alacak.
        return redirect(url_for('index'))

    except Exception as e:
        logging.exception(f"Error in submit_answer for user {user.name}:")
        flash("An error occurred while processing your answer.", "danger")
        return redirect(url_for('index'))


# Reset rotası artık global state ile çalıştığı için anlamsız.
# @app.route('/reset')
# def reset_quiz():
#     # ... (Bu fonksiyon kaldırılabilir veya admin paneline taşınabilir) ...
#     pass

# --- SocketIO Event Handlers ---
@socketio.on('connect')
def handle_connect():
    user = get_current_user()
    if user:
        logging.info(f"User {user.name} connected via SocketIO.")
        # Yeni bağlanan kullanıcıya mevcut soruyu gönder
        global current_question_data
        if current_question_data.get("question"):
            question_payload = current_question_data["question"].to_dict()
            question_payload["end_time"] = current_question_data["end_time"].isoformat() if current_question_data.get("end_time") else None
            emit('new_question', question_payload) # Sadece bağlanan kişiye gönder
            logging.info(f"Sent current question {current_question_data['question_id']} to newly connected user {user.name}")
    else:
        logging.warning("Unauthenticated user connected via SocketIO.")
        # Giriş yapmamış kullanıcıları belki disconnect edebiliriz? Şimdilik loglayalım.

@socketio.on('disconnect')
def handle_disconnect():
    user = get_current_user()
    logging.info(f"User {user.name if user else 'Unknown'} disconnected from SocketIO.")

# --- Background Task for Quiz Timer ---
def background_quiz_timer():
    """Arka planda çalışarak periyodik olarak yeni soru gönderir."""
    global current_question_data
    last_question_id = None
    logging.info("Background quiz timer thread started.")

    with app.app_context(): # Veritabanı erişimi için app context gerekli
        while not stop_event.is_set():
            try:
                # Veritabanından bir sonraki soruyu al (basit sıralama veya rastgele)
                # Önceki sorudan farklı bir soru seçmeye çalışalım
                query = Question.query
                if last_question_id:
                    query = query.filter(Question.id != last_question_id)

                # Basitçe sıradaki ID'yi alalım (daha sofistike olabilir)
                next_question = query.order_by(Question.id).first()
                if not next_question:
                    # Soru kalmadıysa veya ilk soruysa, baştan başla
                    next_question = Question.query.order_by(Question.id).first()

                if next_question:
                    if next_question.id != last_question_id:
                        logging.info(f"Timer: Selecting new question ID: {next_question.id}")
                        current_question_data["question"] = next_question
                        current_question_data["question_id"] = next_question.id
                        current_question_data["end_time"] = datetime.now() + timedelta(seconds=QUESTION_DURATION)
                        last_question_id = next_question.id

                        # Tüm bağlı istemcilere yeni soruyu gönder
                        question_payload = next_question.to_dict()
                        question_payload["end_time"] = current_question_data["end_time"].isoformat()
                        socketio.emit('new_question', question_payload)
                        logging.info(f"Timer: Emitted new_question event for Q_ID: {next_question.id}")
                    else:
                        # Tek soru varsa veya bir hata olduysa logla
                        logging.debug("Timer: No new question found or same question selected.")
                        # Eğer tek soru varsa, süreyi yine de güncelleyebiliriz
                        current_question_data["end_time"] = datetime.now() + timedelta(seconds=QUESTION_DURATION)
                        question_payload = current_question_data["question"].to_dict()
                        question_payload["end_time"] = current_question_data["end_time"].isoformat()
                        socketio.emit('new_question', question_payload) # Süre güncellemesi için tekrar gönder


                else:
                    logging.warning("Timer: No questions found in the database!")
                    # Soru yoksa bir süre bekle
                    stop_event.wait(QUESTION_DURATION) # Hata durumunda CPU'yu yormamak için bekle
                    continue # Döngünün başına dön

            except Exception as e:
                logging.exception("Timer: Error in background quiz timer loop:")
                # Hata durumunda biraz bekle
                stop_event.wait(5)

            # Bir sonraki soruya geçmeden önce bekle
            logging.debug(f"Timer: Waiting for {QUESTION_DURATION} seconds.")
            stop_event.wait(QUESTION_DURATION) # Belirlenen süre kadar bekle

    logging.info("Background quiz timer thread stopped.")


def start_quiz_timer():
    """Arka plan görevini başlatır."""
    global quiz_timer_thread
    if quiz_timer_thread is None or not quiz_timer_thread.is_alive():
        stop_event.clear()
        quiz_timer_thread = threading.Thread(target=background_quiz_timer, daemon=True)
        quiz_timer_thread.start()
        logging.info("Quiz timer background thread initiated.")

# --- Veritabanı Yönetim Komutları ---
# db-create ve db-seed komutları aynı kalıyor,
# ancak User tablosunu da oluşturacaklar.
@app.cli.command('db-create')
def db_create():
    """Veritabanı tablolarını (User ve Question) oluşturur."""
    with app.app_context():
         try:
             db.create_all()
             print("Database tables (User, Question) created successfully!")
         except Exception as e:
             print(f"Error creating database tables: {e}")

@app.cli.command('db-seed')
def db_seed():
    # ... (Önceki seed içeriği aynı) ...
    # Kullanıcı eklemeye gerek yok, login ile oluşacaklar.
    # Sadece soru ekleme kısmı kalabilir.
    with app.app_context():
         try:
            num_deleted = db.session.query(Question).delete()
            print(f"Deleted {num_deleted} existing questions before seeding.")

            # --- Sample Questions ---
            q1 = Question(question_text="What is the capital of France?",
                          option1="Berlin", option2="Madrid", option3="Paris", option4="Rome",
                          correct_answer="Paris")
            q2 = Question(question_text="Which planet is known as the Red Planet?",
                          option1="Earth", option2="Mars", option3="Jupiter", option4="Venus",
                          correct_answer="Mars")
            q3 = Question(question_text="What is the largest ocean on Earth?",
                          option1="Atlantic", option2="Indian", option3="Arctic", option4="Pacific",
                          correct_answer="Pacific")
            q4 = Question(question_text="What is 2 + 2 * 2?",
                          option1="4", option2="6", option3="8", option4="2",
                          correct_answer="6")
            # --- End Sample Questions ---

            db.session.add_all([q1, q2, q3, q4])
            db.session.commit()
            print(f"Database seeded with {Question.query.count()} initial questions!")
         except Exception as e:
            db.session.rollback()
            print(f"Error seeding database: {e}")

# Yerelde çalıştırma
if __name__ == '__main__':
    # Veritabanı tablolarının var olduğundan emin ol (opsiyonel, ama iyi fikir)
    with app.app_context():
        try:
            # Sadece tablo yoksa oluşturmayı deneyebiliriz, ama create_all güvenli olmalı
            db.create_all()
            logging.info("Database tables checked/created.")
            # Başlangıçta soru yoksa uyar
            if Question.query.count() == 0:
                logging.warning("No questions found in the database. Run 'flask db-seed' command.")
        except Exception as e:
            logging.error(f"Error during initial DB check/creation: {e}")

    # Arka plan görevini başlat
    start_quiz_timer()

    # Uygulamayı SocketIO ile çalıştır
    port = int(os.environ.get('PORT', 5001)) # Changed default port to 5001
    logging.info(f"Starting SocketIO server on host 0.0.0.0 port {port}")
    # debug=True SocketIO ile dikkatli kullanılmalı, özellikle production'da False olmalı.
    # Yerel geliştirme için debug=True sorun yaratabilir (reloader thread'i iki kez başlatabilir)
    # Gunicorn gibi bir WSGI sunucusu production için daha iyidir.
    # socketio.run(app, debug=False, host='0.0.0.0', port=port)
    # Yerel test için debug'ı açalım ama dikkatli olalım:
    socketio.run(app, debug=True, host='0.0.0.0', port=port, use_reloader=False) # Reloader'ı kapatmak thread sorununu çözebilir
