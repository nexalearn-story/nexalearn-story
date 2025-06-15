import os
import csv
from os.path import exists
import traceback
from collections import defaultdict
from flask import (
    Flask, render_template, request, redirect,
    flash, url_for, send_from_directory, abort
)
from werkzeug.utils import secure_filename
import mysql.connector
from mysql.connector import Error as MySQLError

app = Flask(__name__)
app.secret_key = 'replace-with-a-secure-key'


UPLOAD_MASTER_PASSWORD = 'sohail123ansari'

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
ALLOWED_AUDIO_EXT = {'mp3', 'wav', 'ogg'}
ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif'}
DB_CONFIG = {
    'host':     'nexalearndatabase-rehanalam786369123456789-b893.b.aivencloud.com',
    'user':     'avnadmin',
    'port':     15611,
    'password': 'AVNS_LSJ1eP25ovnOkdIEKdg',
    'database': 'defaultdb',
    'use_pure': True
}
CATEGORIES = [
    'Story', 'School Lessons', 'University Courses', 'Novels', 'Biographies',
    'Science & Technology', 'History', 'Self-Help & Motivation',
    'Business & Finance', 'Children\'s Stories', 'Poetry', 'Other'
]



os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename, allowed_set):
    return (
        filename
        and '.' in filename
        and filename.rsplit('.', 1)[1].lower() in allowed_set
    )

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except MySQLError as e:
        app.logger.error(f"DB connection error: {e}")
        raise

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/submit', methods=['GET', 'POST'])
def submit():
    if request.method == 'POST':
        try:
  
            provided_password = request.form.get('uploadPassword')
            if provided_password != UPLOAD_MASTER_PASSWORD:
           
                flash("Invalid Upload Password. Access Denied.", 'error')
                return redirect(request.url)

          
            author   = request.form.get('authorName', '').strip()
            story    = request.form.get('storyName', '').strip()
            desc     = request.form.get('description', '').strip()
            ep_count = int(request.form.get('episodeCount', 0))
            cover    = request.files.get('coverImage')
            category = request.form.get('category', 'Story').strip()

            if not author or not story:
                flash("Author name and Story name are required.", 'error')
                return redirect(request.url)
            if not (1 <= ep_count <= 50):
                flash("Episode count must be between 1 and 50.", 'error')
                return redirect(request.url)
            if not cover or not allowed_file(cover.filename, ALLOWED_IMAGE_EXT):
                flash("Valid cover image is required.", 'error')
                return redirect(request.url)

            # Save cover file
            ext = cover.filename.rsplit('.', 1)[1].lower()
            cover_fn = secure_filename(f"{story}_cover.{ext}")
            cover_path = os.path.join(app.config['UPLOAD_FOLDER'], cover_fn)
            cover.save(cover_path)


            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
              INSERT INTO stories
                (story_name, author_name, description, cover_image, category)
              VALUES (%s, %s, %s, %s, %s)
            """, (story, author, desc, cover_fn, category))
            story_id = cursor.lastrowid

            # Process each episode (this part is unchanged)
            for i in range(1, ep_count + 1):
                audio = request.files.get(f"audioFiles{i}")
                img   = request.files.get(f"imageFiles{i}")
                title = request.form.get(f"episodeTitles{i}", '').strip()

                if not title: raise ValueError(f"Title missing for episode {i}")
                if not audio or not allowed_file(audio.filename, ALLOWED_AUDIO_EXT): raise ValueError(f"Valid audio required for episode {i}")
                if not img or not allowed_file(img.filename, ALLOWED_IMAGE_EXT): raise ValueError(f"Valid image required for episode {i}")

                aud_ext = audio.filename.rsplit('.', 1)[1].lower()
                img_ext = img.filename.rsplit('.', 1)[1].lower()
                aud_fn = secure_filename(f"{story}_ep{i}_audio.{aud_ext}")
                img_fn = secure_filename(f"{story}_ep{i}_image.{img_ext}")
                audio.save(os.path.join(app.config['UPLOAD_FOLDER'], aud_fn))
                img.save(os.path.join(app.config['UPLOAD_FOLDER'], img_fn))

                cursor.execute("""
                  INSERT INTO episodes
                    (story_id, episode_number, title, audio_file, image_file)
                  VALUES (%s, %s, %s, %s, %s)
                """, (story_id, i, title, aud_fn, img_fn))

            conn.commit()
            flash('Story and episodes uploaded successfully!', 'success')
            return redirect(url_for('submit'))

        except MySQLError as db_err:
            app.logger.error(f"MySQL error: {db_err}")
            flash("A database error occurred. Please try again later.", 'error')
            return redirect(request.url)
        except ValueError as val_err:
            flash(str(val_err), 'error')
            return redirect(request.url)
        except Exception as ex:
            app.logger.error("Unhandled exception:\n" + traceback.format_exc())
            flash("An unexpected error occurred. Please contact support.", 'error')
            return redirect(request.url)

    # GET request: just render the form
    return render_template('submit.html', categories=CATEGORIES)



@app.route('/')
def index():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT story_id, story_name, author_name, cover_image, category FROM stories ORDER BY story_id DESC")
        all_stories = cursor.fetchall()
        stories_by_category = defaultdict(list)
        for story in all_stories:
            stories_by_category[story['category']].append(story)
        return render_template('index.html', stories_by_category=stories_by_category, categories=CATEGORIES)
    except MySQLError as db_err:
        app.logger.error(f"MySQL error on index: {db_err}")
        return render_template('error.html', message="Could not load stories."), 500
    except Exception as ex:
        app.logger.error("Unhandled exception on index:\n" + traceback.format_exc())
        return render_template('error.html', message="An unexpected error occurred."), 500

@app.route('/story/<int:story_id>')
def story(story_id):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT story_id, story_name, author_name, description, cover_image, category FROM stories WHERE story_id = %s", (story_id,))
    story = cur.fetchone()
    if not story: abort(404)
    cur.execute("SELECT episode_number, title, audio_file, image_file FROM episodes WHERE story_id = %s ORDER BY episode_number", (story_id,))
    episodes = cur.fetchall()
    conn.close()
    return render_template('story.html', story=story, episodes=episodes)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name    = request.form.get('name', '').strip()
        email   = request.form.get('email', '').strip()
        message = request.form.get('message', '').strip()
        filename = 'static/contact-info.csv'
        file_exists = exists(filename)
        with open(filename, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(['Name', 'Email', 'Message'])
            writer.writerow([name, email, message])
        flash("Thanks for reaching out! We'll get back to you soon.", "success")
        return "<h1>Thanks for reaching out! We'll get back to you soon.</h1>"
    return render_template('contact.html')

@app.route('/base')
def base(): return render_template('base.html')
@app.route('/about')
def about(): return render_template('about.html')
@app.route('/privacy')
def privacy(): return render_template('privacy.html')
@app.route('/termsAndCondition')
def termsAndCondition(): return render_template('termsAndCondition.html')

@app.errorhandler(404)
def not_found(e): return render_template('error.html', message="Page not found."), 404
@app.errorhandler(500)
def server_error(e): return render_template('error.html', message="Internal server error."), 500

if __name__ == '__main__':
    # app.run()
    app.run()