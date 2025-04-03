import os
import json # JSON işlemek için
import requests # Facebook API istekleri için
from flask import Flask, render_template, request, redirect, url_for, session, flash # Flash ekledik
from flask_sqlalchemy import SQLAlchemy
import logging

# --- Uygulama ve Yapılandırma ---
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

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
FACEBOOK_REDIRECT_URI = 'https://benim-cafe-quiz.onrender.com/facebook/callback' # <<< KENDİ URL'NİZLE DEĞİŞTİRİN!
FACEBOOK_API_VERSION = 'v18.0' # API sürümünü belirtmek iyi practice'dir

db = SQLAlchemy(app)

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

    # Kullanıcı giriş yapmışsa quiz'i göster
    try:
        # ... (Önceki index fonksiyonundaki quiz mantığı buraya gelecek) ...
        if 'current_question_index' not in session or session.get('quiz_over'):
            logging.info(f"User {user.name} starting/resetting quiz.")
            session['current_question_index'] = 0
            session['score'] = 0
            session['quiz_over'] = False
            session['total_questions'] = Question.query.count()

        q_index = session['current_question_index']
        total_questions = session.get('total_questions', 0)
        error_message = session.pop('error_message', None) # Flash mesajları kullanacağız ama bu kalabilir

        if total_questions == 0:
            flash("Quiz is not ready yet. No questions found!", "warning")
            return render_template('quiz.html', quiz_over=True, current_user=user)
        elif q_index >= total_questions:
            session['quiz_over'] = True
            return render_template('quiz.html', quiz_over=True, final_score=session['score'], total_questions=total_questions, current_user=user)

        current_q = Question.query.order_by(Question.id).offset(q_index).first()
        if not current_q:
             flash("An error occurred while fetching the question.", "danger")
             session['quiz_over'] = True
             return render_template('quiz.html', quiz_over=True, current_user=user)

        return render_template('quiz.html',
                               question=current_q,
                               score=session['score'],
                               q_number=q_index + 1,
                               total_questions=total_questions,
                               quiz_over=False,
                               current_user=user)
    except Exception as e:
         logging.exception("An error occurred in index route for logged in user:")
         flash("An unexpected error occurred. Please try logging in again.", "danger")
         session.clear() # Hata durumunda oturumu temizle
         return redirect(url_for('login_page'))

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

    # ... Önceki submit_answer fonksiyonundaki mantık ...
    try:
        if session.get('quiz_over'):
            return redirect(url_for('index'))

        q_index = session['current_question_index']
        user_answer = request.form.get('answer')

        if not user_answer:
            flash("Please select an answer.", "warning") # Flash mesajı kullan
            # session['error_message'] = "Please select an answer." # Eski yöntem
            return redirect(url_for('index'))

        current_q = Question.query.order_by(Question.id).offset(q_index).first()
        if not current_q:
            flash("Error finding the question.", "danger")
            return redirect(url_for('index'))

        logging.info(f"User {user.name} submitted '{user_answer}' for Q{q_index+1}. Correct: '{current_q.correct_answer}'")
        if user_answer == current_q.correct_answer:
            session['score'] = session.get('score', 0) + 1
            logging.info(f"Correct! User {user.name} score: {session['score']}")
        else:
            logging.info(f"Incorrect answer by user {user.name}.")

        session['current_question_index'] = q_index + 1
        session.modified = True
        return redirect(url_for('index'))

    except Exception as e:
        logging.exception(f"Error in submit_answer for user {user.name}:")
        flash("An error occurred while processing your answer.", "danger")
        return redirect(url_for('index'))


# Reset rotasını da giriş kontrolü ile güncelle (opsiyonel ama mantıklı)
@app.route('/reset')
def reset_quiz():
    user = get_current_user()
    if not user:
        return redirect(url_for('login_page'))

    logging.info(f"User {user.name} resetting quiz.")
    session.pop('current_question_index', None)
    session.pop('score', None)
    session.pop('quiz_over', None)
    session.pop('total_questions', None)
    # session.pop('error_message', None) # Flash kullanıyoruz
    flash("Quiz reset.", "info")
    return redirect(url_for('index'))

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
            # ... (Soruları ekleme kodu) ...
            q1 = Question(...)
            q2 = Question(...)
            q3 = Question(...)
            db.session.add_all([q1, q2, q3])
            db.session.commit()
            print("Database seeded with initial questions!")
         except Exception as e:
            db.session.rollback()
            print(f"Error seeding database: {e}")

# Yerelde çalıştırma kısmı aynı kalabilir
if __name__ == '__main__':
    # ...
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
