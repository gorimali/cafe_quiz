import os # os modülünü import et
from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)

# SECRET_KEY'i ortam değişkeninden al. Bulamazsa, yerel geliştirme için varsayılan bir değer kullan.
# Render'da bu 'SECRET_KEY' değişkenini ayrıca tanımlayacağız.
app.secret_key = os.environ.get('SECRET_KEY', 'rnd_4guTgJJ33jk1w1lMywPwM78fZrdK')

# --- Geri kalan kod aynı kalıyor ---
questions = [
    # ... (sorular aynı) ...
]

@app.route('/')
def index():
    # ... (fonksiyon içeriği aynı) ...

@app.route('/submit_answer', methods=['POST'])
def submit_answer():
    # ... (fonksiyon içeriği aynı) ...

@app.route('/reset')
def reset_quiz():
    # ... (fonksiyon içeriği aynı) ...

# Bu kısım Render tarafından KULLANILMAYACAK, Gunicorn kullanılacak.
# Sadece yerel makinede 'python app.py' ile çalıştırmak için kalabilir.
if __name__ == '__main__':
    # Yerelde çalıştırırken 0.0.0.0 kullanmak, aynı ağdaki başka cihazlardan erişimi sağlar.
    # Render portu dinamik olarak atar, bu yüzden burada port belirtmek Render için önemli değil.
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))