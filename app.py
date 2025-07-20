# app.py

import os
import csv
from os.path import exists
import traceback
from collections import defaultdict
from flask import (
    Flask, render_template, request, redirect,
    flash, url_for, abort
)
from werkzeug.utils import secure_filename
import mysql.connector
from mysql.connector import Error as MySQLError
import boto3

app = Flask(__name__)
app.secret_key = 'replace-with-a-secure-key'


UPLOAD_MASTER_PASSWORD = 'sohail123ansari'

ALLOWED_AUDIO_EXT = {'mp3', 'wav', 'ogg'}
ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif'}

DB_CONFIG = {
    # 'host':     'nexalearndatabase-rehanalam786369123456789-b893.b.aivencloud.com',
    'host':     'nexalearndatabase-rehanalam786369123456789-b893.b.aivencloud.com',
    'user':     'avnadmin',
    'port':     15611,
    # 'password': 'AVNS_LSJ1eP25ovnOkdIEKdg',
    'password': 'AVNS_LSJ1eP25ovnOkdIEKdg',
    'database': 'defaultdb',
    'use_pure': True
}
# access key =AKIAREYLV2GN2NWR45GV
# secret aws_access_key_id=eTOK015JFz0zDYbZ7OzQ6y50Y5grXM6CoE0VEZ5+
# eTOK015JFz0zDYbZ7OzQ6y50Y5grXM6CoE0VEZ5+

# --- IMPORTANT: PUT YOUR NEW, VALID KEYS HERE ---
# --- You must get these from your AWS IAM console after revoking the old ones.
# S3_CONFIG = {
#     'bucket':        'bucketeer-6524d4dc-f696-46f7-9541-e754e7574207',
#     'region':        'eu-west-1',
#     'access_key_id': 'AKIARVGPJVYVII4BKJHS',  # <-- PASTE YOUR NEW KEY ID HERE
#     'secret_key':    'Pu0IIM7STTsOKeI0ijqWdaY388CTQfXNDUAbxwxr'       # <-- PASTE YOUR NEW SECRET KEY HERE
# }

# Access key ID,Secret access key
# AKIAREYLV2GN2NWR45GV ,eTOK015JFz0zDYbZ7OzQ6y50Y5grXM6CoE0VEZ5+
# S3_CONFIG = {
#     'bucket': 'learnivox-bucket',
#     'region': 'ap-south-1',
#     'access_key_id': 'AKIAREYLV2GN2NWR45GV', 
#     'secret_key':    'eTOK015JFz0zDYbZ7OzQ6y50Y5grXM6CoE0VEZ5+'       # <-- PASTE YOUR NEW SECRET KEY HERE
# }

S3_CONFIG = {
    'bucket': 'learnivox-bucket',  # Replace with your actual bucket name
    'region': 'ap-south-1',        # Mumbai region
    'access_key_id': 'AKIAREYLV2GN2NWR45GV',
    'secret_key': 'eTOK015JFz0zDYbZ7OzQ6y50Y5grXM6CoE0VEZ5+'
}

S3_LOCATION = f"https://{S3_CONFIG['bucket']}.s3.{S3_CONFIG['region']}.amazonaws.com/"


CATEGORIES = [
    'Story', 'School Lessons', 'University Courses', 'Novels', 'Biographies',
    'Science & Technology', 'History', 'Self-Help & Motivation',
    'Business & Finance', 'Children\'s Stories', 'Poetry', 'Other'
]

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

def get_s3_client():
    try:
        s3 = boto3.client(
           "s3",
           region_name=S3_CONFIG['region'],
           aws_access_key_id=S3_CONFIG['access_key_id'],
           aws_secret_access_key=S3_CONFIG['secret_key']
        )
        return s3
    except Exception as e:
        app.logger.error(f"S3 client creation error: {e}")
        raise

@app.context_processor
def inject_s3_location():
    return dict(S3_LOCATION=S3_LOCATION)

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

            if not all([author, story]):
                flash("Author name and Story name are required.", 'error')
                return redirect(request.url)
            if not (1 <= ep_count <= 50):
                flash("Episode count must be between 1 and 50.", 'error')
                return redirect(request.url)
            if not cover or not allowed_file(cover.filename, ALLOWED_IMAGE_EXT):
                flash("Valid cover image is required.", 'error')
                return redirect(request.url)

            s3 = get_s3_client()

            ext = cover.filename.rsplit('.', 1)[1].lower()
            cover_fn = secure_filename(f"{story}_cover.{ext}")
            
            # This will now upload the file as PRIVATE, as you requested.
            s3.upload_fileobj(
                cover,
                S3_CONFIG['bucket'],
                cover_fn,
                ExtraArgs={'ContentType': cover.content_type}
            )

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
              INSERT INTO stories
                (story_name, author_name, description, cover_image, category)
              VALUES (%s, %s, %s, %s, %s)
            """, (story, author, desc, cover_fn, category))
            story_id = cursor.lastrowid

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

                # These will also be uploaded as PRIVATE.
                s3.upload_fileobj(
                    audio,
                    S3_CONFIG['bucket'],
                    aud_fn,
                    ExtraArgs={'ContentType': audio.content_type}
                )
                s3.upload_fileobj(
                    img,
                    S3_CONFIG['bucket'],
                    img_fn,
                    ExtraArgs={'ContentType': img.content_type}
                )

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
            if 'boto' in str(ex).lower():
                flash("An error occurred while uploading files to storage. Please check configuration.", 'error')
            else:
                flash("An unexpected error occurred. Please contact support.", 'error')
            return redirect(request.url)

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
        conn.close()
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
    app.run()
