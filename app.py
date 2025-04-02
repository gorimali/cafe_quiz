import os
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
import logging # Loglama ekleyelim

# Uygulama ve loglama kurulumu
app = Flask(__name__)
logging.basicConfig(level=logging.INFO) # Temel loglama

# --- Veritabanı Yapılandırması ---
# Render'ın sağladığı DATABASE_URL ortam değişkenini kullan.
# Eğer yoksa (yerel geliştirme?), varsayılan olarak bir SQLite dosyası kullan.
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    logging.warning("DATABASE_URL not found, using local SQLite.")
    DATABASE_URL = 'sqlite:///local_quiz.db' # Yerel test için
elif DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy psycopg2 için 'postgresql://' bekler
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    logging.info("Using PostgreSQL database.")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Performans için kapat
app.secret_key = os.environ.get('SECRET_KEY', 'cok_gizli_bir_anahtar_123!@#_yerel') # Yerel için varsayılan

db = SQLAlchemy(app) # SQLAlchemy'yi başlat

# --- Veritabanı Modeli (Question Tablosu) ---
class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question_text = db.Column(db.String(500), nullable=False)
    option1 = db.Column(db.String(100), nullable=False)
    option2 = db.Column(db.String(100), nullable=False)
    option3 = db.Column(db.String(100), nullable=False)
    option4 = db.Column(db.String(100), nullable=False)
    correct_answer = db.Column(db.String(100), nullable=False) # Doğru seçeneğin metnini saklıyoruz

    def __repr__(self):
        return f'<Question {self.id}>'

    # HTML şablonunda kolayca kullanmak için seçenekleri liste olarak döndür
    def get_options(self):
        return [self.option1, self.option2, self.option3, self.option4]

# --- Rotalar (Routes) ---

@app.route('/')
def index():
    try:
        # Oturumu başlat/sıfırla
        if 'current_question_index' not in session or session.get('quiz_over'):
            logging.info("Initializing or resetting session.")
            session['current_question_index'] = 0
            session['score'] = 0
            session['quiz_over'] = False
            # Toplam soru sayısını veritabanından alıp session'a ekle
            session['total_questions'] = Question.query.count()
            logging.info(f"Total questions found: {session['total_questions']}")

        q_index = session['current_question_index']
        total_questions = session.get('total_questions', 0)

        # Hata mesajı varsa göster
        error_message = session.pop('error_message', None)

        # Tüm sorular bitti mi veya hiç soru yok mu?
        if total_questions == 0:
            logging.warning("No questions found in the database.")
            return render_template('quiz.html', quiz_over=True, error_message="Quiz is not ready yet. No questions found!")
        elif q_index >= total_questions:
            logging.info("Quiz finished.")
            session['quiz_over'] = True
            return render_template('quiz.html', quiz_over=True, final_score=session['score'], total_questions=total_questions)

        # Mevcut soruyu veritabanından al (ID yerine sıraya göre - basitlik için)
        # Not: Büyük veri setlerinde veya ID atlamalarında bu yöntem verimsiz olabilir.
        current_q = Question.query.order_by(Question.id).offset(q_index).first()

        if not current_q:
             logging.error(f"Could not retrieve question at index {q_index}.")
             session['quiz_over'] = True # Hata durumunda quiz'i bitir
             return render_template('quiz.html', quiz_over=True, error_message="An error occurred while fetching the question.")

        logging.info(f"Displaying question {q_index + 1} (ID: {current_q.id})")
        return render_template('quiz.html',
                               question=current_q,
                               score=session['score'],
                               q_number=q_index + 1,
                               total_questions=total_questions,
                               quiz_over=False,
                               error_message=error_message)
    except Exception as e:
         logging.exception("An error occurred in index route:")
         # Hata durumunda kullanıcıya genel bir mesaj göster
         session['quiz_over'] = True # Hata durumunda quiz'i bitirelim
         return render_template('quiz.html', quiz_over=True, error_message="An unexpected error occurred. Please try again later.")


@app.route('/submit_answer', methods=['POST'])
def submit_answer():
    try:
        if session.get('quiz_over'):
            return redirect(url_for('index'))

        q_index = session['current_question_index']
        user_answer = request.form.get('answer')

        # Seçim yapılmadıysa hata mesajı ile geri dön
        if not user_answer:
             session['error_message'] = "Please select an answer."
             return redirect(url_for('index'))

        # İlgili soruyu veritabanından tekrar al
        current_q = Question.query.order_by(Question.id).offset(q_index).first()

        if not current_q:
            logging.error(f"Could not find question for index {q_index} during submission.")
            return redirect(url_for('index')) # Ana sayfaya yönlendir

        # Cevabı kontrol et ve skoru güncelle
        logging.info(f"Q{q_index+1}: User answered '{user_answer}', Correct is '{current_q.correct_answer}'")
        if user_answer == current_q.correct_answer:
            session['score'] = session.get('score', 0) + 1
            logging.info(f"Correct! Score is now: {session['score']}")
        else:
            logging.info("Incorrect answer.")


        # Bir sonraki soruya geç
        session['current_question_index'] = q_index + 1
        session.modified = True # Session değişikliğini kaydet

        return redirect(url_for('index'))
    except Exception as e:
        logging.exception("An error occurred in submit_answer route:")
        session['error_message'] = "An error occurred while processing your answer."
        return redirect(url_for('index')) # Hata durumunda ana sayfaya


@app.route('/reset')
def reset_quiz():
    logging.info("Resetting quiz.")
    # Session'daki quiz ile ilgili tüm bilgileri temizle
    session.pop('current_question_index', None)
    session.pop('score', None)
    session.pop('quiz_over', None)
    session.pop('total_questions', None)
    session.pop('error_message', None)
    return redirect(url_for('index'))

# --- Veritabanı Yönetim Komutları (Flask CLI ile kullanılır) ---
# Render Shell üzerinden çalıştırmak için:
# flask db-create
# flask db-seed

@app.cli.command('db-create')
def db_create():
    """Veritabanı tablolarını oluşturur."""
    with app.app_context(): # Flask uygulama bağlamı gereklidir
         try:
             db.create_all()
             print("Database tables created successfully!")
         except Exception as e:
             print(f"Error creating database tables: {e}")

@app.cli.command('db-seed')
def db_seed():
    """Veritabanına örnek sorular ekler."""
    with app.app_context():
         try:
            # Önce mevcut soruları silelim (tekrar tekrar eklememek için)
            # Dikkat: Gerçek uygulamada bu tehlikeli olabilir.
            num_deleted = db.session.query(Question).delete()
            print(f"Deleted {num_deleted} existing questions before seeding.")

            q1 = Question(question_text='What is the capital of France?', option1='Berlin', option2='Madrid', option3='Paris', option4='Rome', correct_answer='Paris')
            q2 = Question(question_text='What is 2 * 8?', option1='10', option2='14', option3='16', option4='18', correct_answer='16')
            q3 = Question(question_text='Which planet is known as the Red Planet?', option1='Earth', option2='Mars', option3='Jupiter', option4='Venus', correct_answer='Mars')

            db.session.add_all([q1, q2, q3])
            db.session.commit()
            print("Database seeded with 3 initial questions!")
         except Exception as e:
            db.session.rollback() # Hata olursa işlemi geri al
            print(f"Error seeding database: {e}")

# Gunicorn Render'da uygulamayı başlatacağı için bu kısım genellikle Render'da çalışmaz.
# Yerel testler için kalabilir.
if __name__ == '__main__':
    # Yerel SQLite kullanılıyorsa ve db dosyası yoksa oluştur
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:///') and not os.path.exists(app.config['SQLALCHEMY_DATABASE_URI'].split('///')[1]):
        with app.app_context():
            print("Creating local SQLite database...")
            db.create_all()
            # İsterseniz yerel db'yi otomatik seed edebilirsiniz
            # print("Seeding local database...")
            # db_seed() # Komutu çağırabiliriz
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
