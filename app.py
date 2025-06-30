import os
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
from botocore.client import Config # IMPORTANT: Import Config
from botocore.exceptions import ClientError

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a-default-secret-key-for-development')

# --- CONFIGURATION ---
DB_CONFIG = {
    'host':     os.environ.get('DB_HOST'),
    'user':     os.environ.get('DB_USER'),
    'port':     os.environ.get('DB_PORT'),
    'password': os.environ.get('DB_PASSWORD'),
    'database': os.environ.get('DB_NAME'),
    'use_pure': True
}
S3_BUCKET = os.environ.get('BUCKETEER_BUCKET_NAME')
S3_REGION = os.environ.get('BUCKETEER_AWS_REGION')
S3_ACCESS_KEY_ID = os.environ.get('BUCKETEER_AWS_ACCESS_KEY_ID')
S3_SECRET_KEY = os.environ.get('BUCKETEER_AWS_SECRET_ACCESS_KEY')

UPLOAD_MASTER_PASSWORD = os.environ.get('UPLOAD_MASTER_PASSWORD', 'sohail123ansari')
ALLOWED_AUDIO_EXT = {'mp3', 'wav', 'ogg'}
ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif'}
CATEGORIES = [
    'Story', 'School Lessons', 'University Courses', 'Novels', 'Biographies',
    'Science & Technology', 'History', 'Self-Help & Motivation',
    'Business & Finance', 'Children\'s Stories', 'Poetry', 'Other'
]


# --- HELPER FUNCTIONS ---
def allowed_file(filename, allowed_set):
    return (
        filename
        and '.' in filename
        and filename.rsplit('.', 1)[1].lower() in allowed_set
    )

def get_db_connection():
    if not all(DB_CONFIG.values()):
        app.logger.error("Database configuration is missing.")
        return None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except MySQLError as e:
        app.logger.error(f"DB connection error: {e}")
        return None

def get_s3_client():
    if not all([S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_KEY]):
        app.logger.error("S3 configuration is missing!")
        return None
    try:
        # --- THIS IS THE FIX ---
        # Force the client to use Signature Version 4. This is required for
        # many regions to prevent the 307 -> 403 redirect error.
        s3_config = Config(signature_version='s3v4')
        
        s3 = boto3.client(
           "s3",
           region_name=S3_REGION,
           aws_access_key_id=S3_ACCESS_KEY_ID,
           aws_secret_access_key=S3_SECRET_KEY,
           config=s3_config # Apply the configuration here
        )
        return s3
    except Exception as e:
        app.logger.error(f"S3 client creation error: {e}")
        return None

# --- ROUTES ---
@app.route('/submit', methods=['GET', 'POST'])
def submit():
    # This route uses the original logic. NO 'ACL' parameter is needed.
    if request.method == 'POST':
        try:
            provided_password = request.form.get('uploadPassword')
            if provided_password != UPLOAD_MASTER_PASSWORD:
                flash("Invalid Upload Password. Access Denied.", 'error')
                return redirect(request.url)

            author = request.form.get('authorName', '').strip()
            story_name = request.form.get('storyName', '').strip()
            desc = request.form.get('description', '').strip()
            ep_count = int(request.form.get('episodeCount', 0))
            cover = request.files.get('coverImage')
            category = request.form.get('category', 'Story').strip()

            if not all([author, story_name, cover]) or not (1 <= ep_count <= 50) or not allowed_file(cover.filename, ALLOWED_IMAGE_EXT):
                flash("Author, Story Name, valid Cover Image, and Episode Count (1-50) are required.", 'error')
                return redirect(request.url)

            s3 = get_s3_client()
            conn = get_db_connection()
            if not s3 or not conn:
                flash("Server configuration error. Please contact support.", 'error')
                return redirect(request.url)

            ext = cover.filename.rsplit('.', 1)[1].lower()
            cover_fn = secure_filename(f"{story_name}_cover.{ext}")
            s3.upload_fileobj(cover, S3_BUCKET, cover_fn, ExtraArgs={'ContentType': cover.content_type})

            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO stories (story_name, author_name, description, cover_image, category) VALUES (%s, %s, %s, %s, %s)",
                (story_name, author, desc, cover_fn, category)
            )
            story_id = cursor.lastrowid

            for i in range(1, ep_count + 1):
                audio = request.files.get(f"audioFiles{i}")
                img = request.files.get(f"imageFiles{i}")
                title = request.form.get(f"episodeTitles{i}", '').strip()

                if not title or not audio or not img or not allowed_file(audio.filename, ALLOWED_AUDIO_EXT) or not allowed_file(img.filename, ALLOWED_IMAGE_EXT):
                    raise ValueError(f"Valid title, audio, and image are required for episode {i}")

                aud_ext, img_ext = audio.filename.rsplit('.', 1)[1].lower(), img.filename.rsplit('.', 1)[1].lower()
                aud_fn = secure_filename(f"{story_name}_ep{i}_audio.{aud_ext}")
                img_fn = secure_filename(f"{story_name}_ep{i}_image.{img_ext}")

                s3.upload_fileobj(audio, S3_BUCKET, aud_fn, ExtraArgs={'ContentType': audio.content_type})
                s3.upload_fileobj(img, S3_BUCKET, img_fn, ExtraArgs={'ContentType': img.content_type})

                cursor.execute(
                    "INSERT INTO episodes (story_id, episode_number, title, audio_file, image_file) VALUES (%s, %s, %s, %s, %s)",
                    (story_id, i, title, aud_fn, img_fn)
                )

            conn.commit()
            flash('Story and episodes uploaded successfully!', 'success')
            return redirect(url_for('submit'))

        except (MySQLError, ValueError, ClientError) as err:
            app.logger.error(f"An error occurred during submission: {err}")
            flash(f"An error occurred: {err}", 'error')
            return redirect(request.url)
        except Exception:
            app.logger.error("Unhandled exception during submission:\n" + traceback.format_exc())
            flash("An unexpected server error occurred.", 'error')
            return redirect(request.url)

    return render_template('submit.html', categories=CATEGORIES)


@app.route('/')
def index():
    # This route uses the pre-signed URL logic, which is now fixed.
    try:
        conn = get_db_connection()
        if not conn:
            return render_template('error.html', message="Could not connect to the database."), 500

        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT story_id, story_name, author_name, cover_image, category FROM stories ORDER BY story_id DESC")
        all_stories = cursor.fetchall()
        
        s3 = get_s3_client()
        if s3:
            for story in all_stories:
                story['cover_image_url'] = ''
                if story.get('cover_image'):
                    try:
                        url = s3.generate_presigned_url(
                            'get_object', 
                            Params={'Bucket': S3_BUCKET, 'Key': story['cover_image']}, 
                            ExpiresIn=3600
                        )
                        story['cover_image_url'] = url
                    except ClientError as e:
                        app.logger.error(f"Couldn't generate presigned URL for {story['cover_image']}: {e}")

        stories_by_category = defaultdict(list)
        for story in all_stories:
            stories_by_category[story['category']].append(story)

        return render_template('index.html', stories_by_category=stories_by_category, categories=CATEGORIES)
    except Exception:
        app.logger.error("Unhandled exception on index:\n" + traceback.format_exc())
        return render_template('error.html', message="An unexpected error occurred."), 500
    finally:
        if 'conn' in locals() and conn.is_connected():
            conn.close()


@app.route('/story/<int:story_id>')
def story(story_id):
    # This route also uses the pre-signed URL logic.
    try:
        conn = get_db_connection()
        if not conn:
            return render_template('error.html', message="Could not connect to the database."), 500
            
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT story_id, story_name, author_name, description, cover_image, category FROM stories WHERE story_id = %s", (story_id,))
        story_data = cursor.fetchone()
        
        if not story_data: abort(404)
        
        cursor.execute("SELECT episode_number, title, audio_file, image_file FROM episodes WHERE story_id = %s ORDER BY episode_number", (story_id,))
        episodes = cursor.fetchall()
        
        s3 = get_s3_client()
        if s3:
            if story_data.get('cover_image'):
                try:
                    story_data['cover_image_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': story_data['cover_image']}, ExpiresIn=3600)
                except ClientError as e:
                    app.logger.error(f"Error for story cover {story_data['cover_image']}: {e}")

            for episode in episodes:
                if episode.get('audio_file'):
                    try:
                        episode['audio_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': episode['audio_file']}, ExpiresIn=3600)
                    except ClientError as e:
                        app.logger.error(f"Error for audio {episode['audio_file']}: {e}")
                if episode.get('image_file'):
                    try:
                        episode['image_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': episode['image_file']}, ExpiresIn=3600)
                    except ClientError as e:
                        app.logger.error(f"Error for image {episode['image_file']}: {e}")

        return render_template('story.html', story=story_data, episodes=episodes)
    except Exception:
        app.logger.error(f"Unhandled exception on story page:\n" + traceback.format_exc())
        return render_template('error.html', message="An unexpected error occurred."), 500
    finally:
        if 'conn' in locals() and conn.is_connected():
            conn.close()


# (Your other routes: /contact, /about, etc. are fine as they are)
@app.route('/contact', methods=['GET', 'POST'])
def contact(): return render_template('contact.html')
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
    app.run(debug=True)




# import os
# import csv
# from os.path import exists
# import traceback
# from collections import defaultdict
# from flask import (
#     Flask, render_template, request, redirect,
#     flash, url_for, abort
# )
# from werkzeug.utils import secure_filename
# import mysql.connector
# from mysql.connector import Error as MySQLError
# import boto3
# from botocore.exceptions import ClientError

# app = Flask(__name__)
# # IMPORTANT: This is read from your Heroku Config Vars.
# app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a-default-secret-key-for-development')

# # --- CONFIGURATION ---
# # All secrets are read securely from Heroku's environment variables.

# # --- Database Config ---
# DB_CONFIG = {
#     'host':     os.environ.get('DB_HOST'),
#     'user':     os.environ.get('DB_USER'),
#     'port':     os.environ.get('DB_PORT'),
#     'password': os.environ.get('DB_PASSWORD'),
#     'database': os.environ.get('DB_NAME'),
#     'use_pure': True
# }

# # --- S3 Bucketeer Config ---
# # These are automatically provided by the Heroku Bucketeer add-on.
# S3_BUCKET = os.environ.get('BUCKETEER_BUCKET_NAME')
# S3_REGION = os.environ.get('BUCKETEER_AWS_REGION')
# S3_ACCESS_KEY_ID = os.environ.get('BUCKETEER_AWS_ACCESS_KEY_ID')
# S3_SECRET_KEY = os.environ.get('BUCKETEER_AWS_SECRET_ACCESS_KEY')

# # --- Other Application Settings ---
# UPLOAD_MASTER_PASSWORD = os.environ.get('UPLOAD_MASTER_PASSWORD', 'sohail123ansari')
# ALLOWED_AUDIO_EXT = {'mp3', 'wav', 'ogg'}
# ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif'}
# CATEGORIES = [
#     'Story', 'School Lessons', 'University Courses', 'Novels', 'Biographies',
#     'Science & Technology', 'History', 'Self-Help & Motivation',
#     'Business & Finance', 'Children\'s Stories', 'Poetry', 'Other'
# ]


# # --- HELPER FUNCTIONS ---

# def allowed_file(filename, allowed_set):
#     """Checks if a file has an allowed extension."""
#     return (
#         filename
#         and '.' in filename
#         and filename.rsplit('.', 1)[1].lower() in allowed_set
#     )

# def get_db_connection():
#     """Establishes a connection to the MySQL database."""
#     if not all(DB_CONFIG.values()):
#         app.logger.error("Database configuration is missing from environment variables.")
#         return None
#     try:
#         conn = mysql.connector.connect(**DB_CONFIG)
#         return conn
#     except MySQLError as e:
#         app.logger.error(f"DB connection error: {e}")
#         return None

# def get_s3_client():
#     """Initializes the Boto3 S3 client using Bucketeer credentials."""
#     if not all([S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_KEY]):
#         app.logger.error("S3 configuration is missing! Check Heroku Bucketeer environment variables.")
#         return None
#     try:
#         s3 = boto3.client(
#            "s3",
#            region_name=S3_REGION,
#            aws_access_key_id=S3_ACCESS_KEY_ID,
#            aws_secret_access_key=S3_SECRET_KEY
#         )
#         return s3
#     except Exception as e:
#         app.logger.error(f"S3 client creation error: {e}")
#         return None

# # --- ROUTES ---

# @app.route('/submit', methods=['GET', 'POST'])
# def submit():
#     if request.method == 'POST':
#         try:
#             # Check password
#             provided_password = request.form.get('uploadPassword')
#             if provided_password != UPLOAD_MASTER_PASSWORD:
#                 flash("Invalid Upload Password. Access Denied.", 'error')
#                 return redirect(request.url)

#             # Get form data
#             author = request.form.get('authorName', '').strip()
#             story_name = request.form.get('storyName', '').strip()
#             desc = request.form.get('description', '').strip()
#             ep_count = int(request.form.get('episodeCount', 0))
#             cover = request.files.get('coverImage')
#             category = request.form.get('category', 'Story').strip()

#             # Validation
#             if not all([author, story_name]):
#                 flash("Author name and Story name are required.", 'error')
#                 return redirect(request.url)
#             if not (1 <= ep_count <= 50):
#                 flash("Episode count must be between 1 and 50.", 'error')
#                 return redirect(request.url)
#             if not cover or not allowed_file(cover.filename, ALLOWED_IMAGE_EXT):
#                 flash("Valid cover image is required.", 'error')
#                 return redirect(request.url)

#             # Get clients
#             s3 = get_s3_client()
#             conn = get_db_connection()
#             if not s3 or not conn:
#                 flash("Server configuration error. Please contact support.", 'error')
#                 return redirect(request.url)

#             # Upload cover image
#             ext = cover.filename.rsplit('.', 1)[1].lower()
#             cover_fn = secure_filename(f"{story_name}_cover.{ext}")
#             s3.upload_fileobj(cover, S3_BUCKET, cover_fn, ExtraArgs={'ContentType': cover.content_type})

#             # Insert story into database
#             cursor = conn.cursor()
#             cursor.execute(
#                 "INSERT INTO stories (story_name, author_name, description, cover_image, category) VALUES (%s, %s, %s, %s, %s)",
#                 (story_name, author, desc, cover_fn, category)
#             )
#             story_id = cursor.lastrowid

#             # Loop through and upload episodes
#             for i in range(1, ep_count + 1):
#                 audio = request.files.get(f"audioFiles{i}")
#                 img = request.files.get(f"imageFiles{i}")
#                 title = request.form.get(f"episodeTitles{i}", '').strip()

#                 if not title or not audio or not img or not allowed_file(audio.filename, ALLOWED_AUDIO_EXT) or not allowed_file(img.filename, ALLOWED_IMAGE_EXT):
#                     raise ValueError(f"Valid title, audio, and image are required for episode {i}")

#                 aud_ext, img_ext = audio.filename.rsplit('.', 1)[1].lower(), img.filename.rsplit('.', 1)[1].lower()
#                 aud_fn = secure_filename(f"{story_name}_ep{i}_audio.{aud_ext}")
#                 img_fn = secure_filename(f"{story_name}_ep{i}_image.{img_ext}")

#                 s3.upload_fileobj(audio, S3_BUCKET, aud_fn, ExtraArgs={'ContentType': audio.content_type})
#                 s3.upload_fileobj(img, S3_BUCKET, img_fn, ExtraArgs={'ContentType': img.content_type})

#                 cursor.execute(
#                     "INSERT INTO episodes (story_id, episode_number, title, audio_file, image_file) VALUES (%s, %s, %s, %s, %s)",
#                     (story_id, i, title, aud_fn, img_fn)
#                 )

#             conn.commit()
#             flash('Story and episodes uploaded successfully!', 'success')
#             return redirect(url_for('submit'))

#         except (MySQLError, ValueError, ClientError) as err:
#             app.logger.error(f"An error occurred during submission: {err}")
#             flash(f"An error occurred: {err}", 'error')
#             return redirect(request.url)
#         except Exception:
#             app.logger.error("Unhandled exception during submission:\n" + traceback.format_exc())
#             flash("An unexpected server error occurred.", 'error')
#             return redirect(request.url)
#         finally:
#             if 'conn' in locals() and conn.is_connected():
#                 cursor.close()
#                 conn.close()

#     return render_template('submit.html', categories=CATEGORIES)


# @app.route('/')
# def index():
#     try:
#         conn = get_db_connection()
#         if not conn:
#             return render_template('error.html', message="Could not connect to the database."), 500

#         cursor = conn.cursor(dictionary=True)
#         cursor.execute("SELECT story_id, story_name, author_name, cover_image, category FROM stories ORDER BY story_id DESC")
#         all_stories = cursor.fetchall()
        
#         s3 = get_s3_client()
#         if s3:
#             for story in all_stories:
#                 story['cover_image_url'] = ''  # Initialize key to prevent template crash
#                 if story.get('cover_image'):
#                     try:
#                         # This generates the temporary, secure URL for a private S3 object
#                         url = s3.generate_presigned_url(
#                             'get_object', 
#                             Params={'Bucket': S3_BUCKET, 'Key': story['cover_image']}, 
#                             ExpiresIn=3600  # URL is valid for 1 hour
#                         )
#                         story['cover_image_url'] = url
#                     except ClientError as e:
#                         # This log is crucial for debugging! Check it via `heroku logs --tail`
#                         app.logger.error(f"Couldn't generate presigned URL for {story['cover_image']}: {e}")

#         stories_by_category = defaultdict(list)
#         for story in all_stories:
#             stories_by_category[story['category']].append(story)

#         return render_template('index.html', stories_by_category=stories_by_category, categories=CATEGORIES)
#     except Exception:
#         app.logger.error("Unhandled exception on index:\n" + traceback.format_exc())
#         return render_template('error.html', message="An unexpected error occurred."), 500
#     finally:
#         if 'conn' in locals() and conn.is_connected():
#             cursor.close()
#             conn.close()


# @app.route('/story/<int:story_id>')
# def story(story_id):
#     try:
#         conn = get_db_connection()
#         if not conn:
#             return render_template('error.html', message="Could not connect to the database."), 500
            
#         cursor = conn.cursor(dictionary=True)
#         cursor.execute("SELECT story_id, story_name, author_name, description, cover_image, category FROM stories WHERE story_id = %s", (story_id,))
#         story_data = cursor.fetchone()
        
#         if not story_data:
#             abort(404)
        
#         cursor.execute("SELECT episode_number, title, audio_file, image_file FROM episodes WHERE story_id = %s ORDER BY episode_number", (story_id,))
#         episodes = cursor.fetchall()

#         s3 = get_s3_client()
#         if s3:
#             # Generate presigned URL for the main story cover
#             story_data['cover_image_url'] = ''
#             if story_data.get('cover_image'):
#                 try:
#                     story_data['cover_image_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': story_data['cover_image']}, ExpiresIn=3600)
#                 except ClientError as e:
#                     app.logger.error(f"Error for story cover {story_data['cover_image']}: {e}")

#             # Generate presigned URLs for each episode
#             for episode in episodes:
#                 episode['audio_url'], episode['image_url'] = '', ''
#                 if episode.get('audio_file'):
#                     try:
#                         episode['audio_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': episode['audio_file']}, ExpiresIn=3600)
#                     except ClientError as e:
#                         app.logger.error(f"Error for audio {episode['audio_file']}: {e}")
#                 if episode.get('image_file'):
#                     try:
#                         episode['image_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': episode['image_file']}, ExpiresIn=3600)
#                     except ClientError as e:
#                         app.logger.error(f"Error for image {episode['image_file']}: {e}")

#         return render_template('story.html', story=story_data, episodes=episodes)
#     except Exception:
#         app.logger.error(f"Unhandled exception on story page:\n" + traceback.format_exc())
#         return render_template('error.html', message="An unexpected error occurred."), 500
#     finally:
#         if 'conn' in locals() and conn.is_connected():
#             cursor.close()
#             conn.close()


# @app.route('/contact', methods=['GET', 'POST'])
# def contact():
#     # This route's code was not provided, but it renders the template.
#     return render_template('contact.html')

# # --- Other Pages & Error Handlers ---
# @app.route('/base')
# def base(): return render_template('base.html')
# @app.route('/about')
# def about(): return render_template('about.html')
# @app.route('/privacy')
# def privacy(): return render_template('privacy.html')
# @app.route('/termsAndCondition')
# def termsAndCondition(): return render_template('termsAndCondition.html')

# @app.errorhandler(404)
# def not_found(e): return render_template('error.html', message="Page not found."), 404
# @app.errorhandler(500)
# def server_error(e): return render_template('error.html', message="Internal server error."), 500

# if __name__ == '__main__':
#     # This setting is for local development. Heroku uses Gunicorn.
#     app.run(debug=True)



# # import os
# # import csv
# # from os.path import exists
# # import traceback
# # from collections import defaultdict
# # from flask import (
# #     Flask, render_template, request, redirect,
# #     flash, url_for, abort
# # )
# # from werkzeug.utils import secure_filename
# # import mysql.connector
# # from mysql.connector import Error as MySQLError
# # import boto3
# # from botocore.exceptions import ClientError

# # app = Flask(__name__)
# # # IMPORTANT: For production, you should set this in your Heroku Config Vars
# # app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a-default-secret-key-for-development')

# # # --- CONFIGURATION ---
# # # All secrets are now read securely from Heroku's environment variables.
# # # DO NOT paste your secrets directly in the code.

# # # --- Database Config ---
# # DB_CONFIG = {
# #     'host':     os.environ.get('DB_HOST'),
# #     'user':     os.environ.get('DB_USER'),
# #     'port':     os.environ.get('DB_PORT'),
# #     'password': os.environ.get('DB_PASSWORD'),
# #     'database': os.environ.get('DB_NAME'),
# #     'use_pure': True
# # }

# # # --- S3 Bucketeer Config ---
# # S3_BUCKET = os.environ.get('BUCKETEER_BUCKET_NAME')
# # S3_REGION = os.environ.get('BUCKETEER_AWS_REGION')
# # S3_ACCESS_KEY_ID = os.environ.get('BUCKETEER_AWS_ACCESS_KEY_ID')
# # S3_SECRET_KEY = os.environ.get('BUCKETEER_AWS_SECRET_ACCESS_KEY')

# # # --- Other Application Settings ---
# # UPLOAD_MASTER_PASSWORD = os.environ.get('UPLOAD_MASTER_PASSWORD', 'sohail123ansari')
# # ALLOWED_AUDIO_EXT = {'mp3', 'wav', 'ogg'}
# # ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif'}
# # CATEGORIES = [
# #     'Story', 'School Lessons', 'University Courses', 'Novels', 'Biographies',
# #     'Science & Technology', 'History', 'Self-Help & Motivation',
# #     'Business & Finance', 'Children\'s Stories', 'Poetry', 'Other'
# # ]


# # # --- HELPER FUNCTIONS ---

# # def allowed_file(filename, allowed_set):
# #     return (
# #         filename
# #         and '.' in filename
# #         and filename.rsplit('.', 1)[1].lower() in allowed_set
# #     )

# # def get_db_connection():
# #     # Check if all required database environment variables are set
# #     if not all(DB_CONFIG.values()):
# #         app.logger.error("Database configuration is missing from environment variables.")
# #         return None
# #     try:
# #         conn = mysql.connector.connect(**DB_CONFIG)
# #         return conn
# #     except MySQLError as e:
# #         app.logger.error(f"DB connection error: {e}")
# #         return None # Return None on failure

# # def get_s3_client():
# #     # Check if all required S3 environment variables are set
# #     if not all([S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_KEY]):
# #         app.logger.error("S3 configuration is missing! Check Heroku Bucketeer environment variables.")
# #         return None # Return None on failure
# #     try:
# #         s3 = boto3.client(
# #            "s3",
# #            region_name=S3_REGION,
# #            aws_access_key_id=S3_ACCESS_KEY_ID,
# #            aws_secret_access_key=S3_SECRET_KEY
# #         )
# #         return s3
# #     except Exception as e:
# #         app.logger.error(f"S3 client creation error: {e}")
# #         return None # Return None on failure

# # # --- ROUTES ---

# # @app.route('/submit', methods=['GET', 'POST'])
# # def submit():
# #     if request.method == 'POST':
# #         try:
# #             # Check password
# #             provided_password = request.form.get('uploadPassword')
# #             if provided_password != UPLOAD_MASTER_PASSWORD:
# #                 flash("Invalid Upload Password. Access Denied.", 'error')
# #                 return redirect(request.url)

# #             # Get form data
# #             author = request.form.get('authorName', '').strip()
# #             story_name = request.form.get('storyName', '').strip()
# #             desc = request.form.get('description', '').strip()
# #             ep_count = int(request.form.get('episodeCount', 0))
# #             cover = request.files.get('coverImage')
# #             category = request.form.get('category', 'Story').strip()

# #             # Validation
# #             if not all([author, story_name]):
# #                 flash("Author name and Story name are required.", 'error')
# #                 return redirect(request.url)
# #             if not (1 <= ep_count <= 50):
# #                 flash("Episode count must be between 1 and 50.", 'error')
# #                 return redirect(request.url)
# #             if not cover or not allowed_file(cover.filename, ALLOWED_IMAGE_EXT):
# #                 flash("Valid cover image is required.", 'error')
# #                 return redirect(request.url)

# #             # Get clients
# #             s3 = get_s3_client()
# #             conn = get_db_connection()
# #             if not s3 or not conn:
# #                 flash("Server configuration error. Please contact support.", 'error')
# #                 return redirect(request.url)

# #             # Upload cover image
# #             ext = cover.filename.rsplit('.', 1)[1].lower()
# #             cover_fn = secure_filename(f"{story_name}_cover.{ext}")
# #             s3.upload_fileobj(cover, S3_BUCKET, cover_fn, ExtraArgs={'ContentType': cover.content_type})

# #             # Insert story into database
# #             cursor = conn.cursor()
# #             cursor.execute(
# #                 "INSERT INTO stories (story_name, author_name, description, cover_image, category) VALUES (%s, %s, %s, %s, %s)",
# #                 (story_name, author, desc, cover_fn, category)
# #             )
# #             story_id = cursor.lastrowid

# #             # Loop through and upload episodes
# #             for i in range(1, ep_count + 1):
# #                 audio = request.files.get(f"audioFiles{i}")
# #                 img = request.files.get(f"imageFiles{i}")
# #                 title = request.form.get(f"episodeTitles{i}", '').strip()

# #                 if not title or not audio or not img or not allowed_file(audio.filename, ALLOWED_AUDIO_EXT) or not allowed_file(img.filename, ALLOWED_IMAGE_EXT):
# #                     raise ValueError(f"Valid title, audio, and image are required for episode {i}")

# #                 aud_ext, img_ext = audio.filename.rsplit('.', 1)[1].lower(), img.filename.rsplit('.', 1)[1].lower()
# #                 aud_fn = secure_filename(f"{story_name}_ep{i}_audio.{aud_ext}")
# #                 img_fn = secure_filename(f"{story_name}_ep{i}_image.{img_ext}")

# #                 s3.upload_fileobj(audio, S3_BUCKET, aud_fn, ExtraArgs={'ContentType': audio.content_type})
# #                 s3.upload_fileobj(img, S3_BUCKET, img_fn, ExtraArgs={'ContentType': img.content_type})

# #                 cursor.execute(
# #                     "INSERT INTO episodes (story_id, episode_number, title, audio_file, image_file) VALUES (%s, %s, %s, %s, %s)",
# #                     (story_id, i, title, aud_fn, img_fn)
# #                 )

# #             conn.commit()
# #             flash('Story and episodes uploaded successfully!', 'success')
# #             return redirect(url_for('submit'))

# #         except (MySQLError, ValueError, ClientError) as err:
# #             app.logger.error(f"An error occurred during submission: {err}")
# #             flash(f"An error occurred: {err}", 'error')
# #             return redirect(request.url)
# #         except Exception:
# #             app.logger.error("Unhandled exception during submission:\n" + traceback.format_exc())
# #             flash("An unexpected server error occurred.", 'error')
# #             return redirect(request.url)

# #     return render_template('submit.html', categories=CATEGORIES)


# # @app.route('/')
# # def index():
# #     try:
# #         conn = get_db_connection()
# #         if not conn:
# #             return render_template('error.html', message="Could not connect to the database."), 500

# #         cursor = conn.cursor(dictionary=True)
# #         cursor.execute("SELECT story_id, story_name, author_name, cover_image, category FROM stories ORDER BY story_id DESC")
# #         all_stories = cursor.fetchall()
# #         conn.close()

# #         s3 = get_s3_client()
# #         if s3:
# #             for story in all_stories:
# #                 story['cover_image_url'] = ''  # Initialize key to prevent template crash
# #                 if story.get('cover_image'):
# #                     try:
# #                         url = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': story['cover_image']}, ExpiresIn=3600)
# #                         story['cover_image_url'] = url
# #                     except ClientError as e:
# #                         app.logger.error(f"Couldn't generate presigned URL for {story['cover_image']}: {e}")

# #         stories_by_category = defaultdict(list)
# #         for story in all_stories:
# #             stories_by_category[story['category']].append(story)

# #         return render_template('index.html', stories_by_category=stories_by_category, categories=CATEGORIES)
# #     except Exception:
# #         app.logger.error("Unhandled exception on index:\n" + traceback.format_exc())
# #         return render_template('error.html', message="An unexpected error occurred."), 500


# # @app.route('/story/<int:story_id>')
# # def story(story_id):
# #     try:
# #         conn = get_db_connection()
# #         if not conn:
# #             return render_template('error.html', message="Could not connect to the database."), 500
            
# #         cursor = conn.cursor(dictionary=True)
# #         cursor.execute("SELECT story_id, story_name, author_name, description, cover_image, category FROM stories WHERE story_id = %s", (story_id,))
# #         story_data = cursor.fetchone()
        
# #         if not story_data:
# #             abort(404)
        
# #         cursor.execute("SELECT episode_number, title, audio_file, image_file FROM episodes WHERE story_id = %s ORDER BY episode_number", (story_id,))
# #         episodes = cursor.fetchall()
# #         conn.close()

# #         s3 = get_s3_client()
# #         if s3:
# #             # Generate presigned URL for the main story cover
# #             story_data['cover_image_url'] = ''
# #             if story_data.get('cover_image'):
# #                 try:
# #                     story_data['cover_image_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': story_data['cover_image']}, ExpiresIn=3600)
# #                 except ClientError as e:
# #                     app.logger.error(f"Error for story cover {story_data['cover_image']}: {e}")

# #             # Generate presigned URLs for each episode
# #             for episode in episodes:
# #                 episode['audio_url'], episode['image_url'] = '', ''
# #                 if episode.get('audio_file'):
# #                     try:
# #                         episode['audio_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': episode['audio_file']}, ExpiresIn=3600)
# #                     except ClientError as e:
# #                         app.logger.error(f"Error for audio {episode['audio_file']}: {e}")
# #                 if episode.get('image_file'):
# #                     try:
# #                         episode['image_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': episode['image_file']}, ExpiresIn=3600)
# #                     except ClientError as e:
# #                         app.logger.error(f"Error for image {episode['image_file']}: {e}")

# #         return render_template('story.html', story=story_data, episodes=episodes)
# #     except Exception:
# #         app.logger.error(f"Unhandled exception on story page:\n" + traceback.format_exc())
# #         return render_template('error.html', message="An unexpected error occurred."), 500


# # @app.route('/contact', methods=['GET', 'POST'])
# # def contact():
# #     # ... (Your contact code is fine, no changes needed)
# #     return render_template('contact.html')

# # # --- Other Pages & Error Handlers (No changes needed) ---
# # @app.route('/base')
# # def base(): return render_template('base.html')
# # @app.route('/about')
# # def about(): return render_template('about.html')
# # @app.route('/privacy')
# # def privacy(): return render_template('privacy.html')
# # @app.route('/termsAndCondition')
# # def termsAndCondition(): return render_template('termsAndCondition.html')

# # @app.errorhandler(404)
# # def not_found(e): return render_template('error.html', message="Page not found."), 404
# # @app.errorhandler(500)
# # def server_error(e): return render_template('error.html', message="Internal server error."), 500

# # if __name__ == '__main__':
# #     # This setting is for local development. Heroku uses its own web server (Gunicorn).
# #     app.run(debug=True)



# # # app.py

# # # import os
# # # import csv
# # # from os.path import exists
# # # import traceback
# # # from collections import defaultdict
# # # from flask import (
# # #     Flask, render_template, request, redirect,
# # #     flash, url_for, abort
# # # )
# # # from werkzeug.utils import secure_filename
# # # import mysql.connector
# # # from mysql.connector import Error as MySQLError
# # # import boto3
# # # from botocore.exceptions import ClientError

# # # app = Flask(__name__)
# # # app.secret_key = 'replace-with-a-secure-key'


# # # UPLOAD_MASTER_PASSWORD = 'sohail123ansari'

# # # ALLOWED_AUDIO_EXT = {'mp3', 'wav', 'ogg'}
# # # ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif'}

# # # DB_CONFIG = {
# # #     'host':     'nexalearndatabase-rehanalam786369123456789-b893.b.aivencloud.com',
# # #     'user':     'avnadmin',
# # #     'port':     15611,
# # #     'password': 'AVNS_LSJ1eP25ovnOkdIEKdg',
# # #     'database': 'defaultdb',
# # #     'use_pure': True
# # # }

# # # # --- IMPORTANT: PUT YOUR NEW, VALID KEYS HERE ---
# # # # --- You must get these from your AWS IAM console after revoking the old ones.
# # # S3_CONFIG = {
# # #     'bucket':        'bucketeer-f6b9dc5a-6fca-4e39-971c-ca53a1778251',
# # #     'region':        'eu-west-1',
# # #     'access_key_id': 'AKIARVGPJVYVLEZLSYXQ',  # <-- PASTE YOUR NEW KEY ID HERE
# # #     'secret_key':    'JGzMpB4mNhhdNXtK2cCsdLeBdIsb4w2+n71tqG1W'       # <-- PASTE YOUR NEW SECRET KEY HERE
# # # }
# # # S3_LOCATION = f"https://{S3_CONFIG['bucket']}.s3.{S3_CONFIG['region']}.amazonaws.com/"


# # # CATEGORIES = [
# # #     'Story', 'School Lessons', 'University Courses', 'Novels', 'Biographies',
# # #     'Science & Technology', 'History', 'Self-Help & Motivation',
# # #     'Business & Finance', 'Children\'s Stories', 'Poetry', 'Other'
# # # ]

# # # def allowed_file(filename, allowed_set):
# # #     return (
# # #         filename
# # #         and '.' in filename
# # #         and filename.rsplit('.', 1)[1].lower() in allowed_set
# # #     )

# # # def get_db_connection():
# # #     try:
# # #         conn = mysql.connector.connect(**DB_CONFIG)
# # #         return conn
# # #     except MySQLError as e:
# # #         app.logger.error(f"DB connection error: {e}")
# # #         raise

# # # def get_s3_client():
# # #     try:
# # #         s3 = boto3.client(
# # #            "s3",
# # #            region_name=S3_CONFIG['region'],
# # #            aws_access_key_id=S3_CONFIG['access_key_id'],
# # #            aws_secret_access_key=S3_CONFIG['secret_key']
# # #         )
# # #         return s3
# # #     except Exception as e:
# # #         app.logger.error(f"S3 client creation error: {e}")
# # #         raise

# # # @app.context_processor
# # # def inject_s3_location():
# # #     return dict(S3_LOCATION=S3_LOCATION)

# # # @app.route('/submit', methods=['GET', 'POST'])
# # # def submit():
# # #     if request.method == 'POST':
# # #         try:
# # #             provided_password = request.form.get('uploadPassword')
# # #             if provided_password != UPLOAD_MASTER_PASSWORD:
# # #                 flash("Invalid Upload Password. Access Denied.", 'error')
# # #                 return redirect(request.url)

# # #             author   = request.form.get('authorName', '').strip()
# # #             story    = request.form.get('storyName', '').strip()
# # #             desc     = request.form.get('description', '').strip()
# # #             ep_count = int(request.form.get('episodeCount', 0))
# # #             cover    = request.files.get('coverImage')
# # #             category = request.form.get('category', 'Story').strip()

# # #             if not all([author, story]):
# # #                 flash("Author name and Story name are required.", 'error')
# # #                 return redirect(request.url)
# # #             if not (1 <= ep_count <= 50):
# # #                 flash("Episode count must be between 1 and 50.", 'error')
# # #                 return redirect(request.url)
# # #             if not cover or not allowed_file(cover.filename, ALLOWED_IMAGE_EXT):
# # #                 flash("Valid cover image is required.", 'error')
# # #                 return redirect(request.url)

# # #             s3 = get_s3_client()

# # #             ext = cover.filename.rsplit('.', 1)[1].lower()
# # #             cover_fn = secure_filename(f"{story}_cover.{ext}")
            
# # #             # This will now upload the file as PRIVATE, as you requested.
# # #             s3.upload_fileobj(
# # #                 cover,
# # #                 S3_CONFIG['bucket'],
# # #                 cover_fn,
# # #                 ExtraArgs={'ContentType': cover.content_type}
# # #             )

# # #             conn = get_db_connection()
# # #             cursor = conn.cursor()
# # #             cursor.execute("""
# # #               INSERT INTO stories
# # #                 (story_name, author_name, description, cover_image, category)
# # #               VALUES (%s, %s, %s, %s, %s)
# # #             """, (story, author, desc, cover_fn, category))
# # #             story_id = cursor.lastrowid

# # #             for i in range(1, ep_count + 1):
# # #                 audio = request.files.get(f"audioFiles{i}")
# # #                 img   = request.files.get(f"imageFiles{i}")
# # #                 title = request.form.get(f"episodeTitles{i}", '').strip()

# # #                 if not title: raise ValueError(f"Title missing for episode {i}")
# # #                 if not audio or not allowed_file(audio.filename, ALLOWED_AUDIO_EXT): raise ValueError(f"Valid audio required for episode {i}")
# # #                 if not img or not allowed_file(img.filename, ALLOWED_IMAGE_EXT): raise ValueError(f"Valid image required for episode {i}")

# # #                 aud_ext = audio.filename.rsplit('.', 1)[1].lower()
# # #                 img_ext = img.filename.rsplit('.', 1)[1].lower()
# # #                 aud_fn = secure_filename(f"{story}_ep{i}_audio.{aud_ext}")
# # #                 img_fn = secure_filename(f"{story}_ep{i}_image.{img_ext}")

# # #                 # These will also be uploaded as PRIVATE.
# # #                 s3.upload_fileobj(
# # #                     audio,
# # #                     S3_CONFIG['bucket'],
# # #                     aud_fn,
# # #                     ExtraArgs={'ContentType': audio.content_type}
# # #                 )
# # #                 s3.upload_fileobj(
# # #                     img,
# # #                     S3_CONFIG['bucket'],
# # #                     img_fn,
# # #                     ExtraArgs={'ContentType': img.content_type}
# # #                 )

# # #                 cursor.execute("""
# # #                   INSERT INTO episodes
# # #                     (story_id, episode_number, title, audio_file, image_file)
# # #                   VALUES (%s, %s, %s, %s, %s)
# # #                 """, (story_id, i, title, aud_fn, img_fn))

# # #             conn.commit()
# # #             flash('Story and episodes uploaded successfully!', 'success')
# # #             return redirect(url_for('submit'))

# # #         except MySQLError as db_err:
# # #             app.logger.error(f"MySQL error: {db_err}")
# # #             flash("A database error occurred. Please try again later.", 'error')
# # #             return redirect(request.url)
# # #         except ValueError as val_err:
# # #             flash(str(val_err), 'error')
# # #             return redirect(request.url)
# # #         except Exception as ex:
# # #             app.logger.error("Unhandled exception:\n" + traceback.format_exc())
# # #             if 'boto' in str(ex).lower():
# # #                 flash("An error occurred while uploading files to storage. Please check configuration.", 'error')
# # #             else:
# # #                 flash("An unexpected error occurred. Please contact support.", 'error')
# # #             return redirect(request.url)

# # #     return render_template('submit.html', categories=CATEGORIES)


# # # # @app.route('/')
# # # # def index():
# # # #     try:
# # # #         conn = get_db_connection()
# # # #         cursor = conn.cursor(dictionary=True)
# # # #         cursor.execute("SELECT story_id, story_name, author_name, cover_image, category FROM stories ORDER BY story_id DESC")
# # # #         all_stories = cursor.fetchall()
# # # #         stories_by_category = defaultdict(list)
# # # #         for story in all_stories:
# # # #             stories_by_category[story['category']].append(story)
# # # #         conn.close()
# # # #         return render_template('index.html', stories_by_category=stories_by_category, categories=CATEGORIES)
# # # #     except MySQLError as db_err:
# # # #         app.logger.error(f"MySQL error on index: {db_err}")
# # # #         return render_template('error.html', message="Could not load stories."), 500
# # # #     except Exception as ex:
# # # #         app.logger.error("Unhandled exception on index:\n" + traceback.format_exc())
# # # #         return render_template('error.html', message="An unexpected error occurred."), 500

# # # # @app.route('/')
# # # # def index():
# # # #     try:
# # # #         conn = get_db_connection()
# # # #         cursor = conn.cursor(dictionary=True)
# # # #         # It's good practice to only select the columns you need
# # # #         cursor.execute("SELECT story_id, story_name, author_name, cover_image, category FROM stories ORDER BY story_id DESC")
# # # #         all_stories = cursor.fetchall()
# # # #         conn.close()

# # # #         # Get the S3 client to generate URLs
# # # #         s3 = get_s3_client()

# # # #         # For each story, generate a temporary URL for its cover image
# # # #         for story in all_stories:
# # # #             # Check if a cover image filename exists
# # # #             if story.get('cover_image'):
# # # #                 try:
# # # #                     # Generate a URL valid for 1 hour (3600 seconds)
# # # #                     url = s3.generate_presigned_url(
# # # #                         'get_object',
# # # #                         Params={'Bucket': S3_CONFIG['bucket'], 'Key': story['cover_image']},
# # # #                         ExpiresIn=3600
# # # #                     )
# # # #                     # Add this new temporary URL to the story dictionary
# # # #                     story['cover_image_url'] = url
# # # #                 except ClientError as e:
# # # #                     app.logger.error(f"Couldn't generate presigned URL for {story['cover_image']}: {e}")
# # # #                     # You could add a placeholder URL in case of an error
# # # #                     story['cover_image_url'] = ''

# # # #         stories_by_category = defaultdict(list)
# # # #         for story in all_stories:
# # # #             stories_by_category[story['category']].append(story)

# # # #         return render_template('index.html', stories_by_category=stories_by_category, categories=CATEGORIES)
# # # #     except Exception as ex:
# # # #         app.logger.error("Unhandled exception on index:\n" + traceback.format_exc())
# # # #         return render_template('error.html', message="An unexpected error occurred."), 500
# # # # In app.py, REPLACE your current index() function with this one:

# # # @app.route('/')
# # # def index():
# # #     try:
# # #         conn = get_db_connection()
# # #         cursor = conn.cursor(dictionary=True)
# # #         cursor.execute("SELECT story_id, story_name, author_name, cover_image, category FROM stories ORDER BY story_id DESC")
# # #         all_stories = cursor.fetchall()
# # #         conn.close()

# # #         s3 = get_s3_client()

# # #         for story in all_stories:
# # #             # --- FIX ---
# # #             # 1. First, set a default empty URL for every story.
# # #             story['cover_image_url'] = ''
            
# # #             # 2. If a cover image exists, try to create a real URL and overwrite the empty one.
# # #             if story.get('cover_image'):
# # #                 try:
# # #                     url = s3.generate_presigned_url(
# # #                         'get_object',
# # #                         Params={'Bucket': S3_CONFIG['bucket'], 'Key': story['cover_image']},
# # #                         ExpiresIn=3600  # URL is valid for 1 hour
# # #                     )
# # #                     story['cover_image_url'] = url
# # #                 except ClientError as e:
# # #                     app.logger.error(f"Couldn't generate presigned URL for {story['cover_image']}: {e}")
# # #                     # If an error occurs, the URL remains an empty string, preventing a crash.

# # #         stories_by_category = defaultdict(list)
# # #         for story in all_stories:
# # #             stories_by_category[story['category']].append(story)

# # #         return render_template('index.html', stories_by_category=stories_by_category, categories=CATEGORIES)
# # #     except Exception as ex:
# # #         app.logger.error("Unhandled exception on index:\n" + traceback.format_exc())
# # #         return render_template('error.html', message="An unexpected error occurred."), 500


# # # @app.route('/story/<int:story_id>')
# # # def story(story_id):
# # #     conn = get_db_connection()
# # #     cursor = conn.cursor(dictionary=True)
# # #     cursor.execute("SELECT story_id, story_name, author_name, description, cover_image, category FROM stories WHERE story_id = %s", (story_id,))
# # #     story_data = cursor.fetchone()
# # #     if not story_data: abort(404)
    
# # #     cursor.execute("SELECT episode_number, title, audio_file, image_file FROM episodes WHERE story_id = %s ORDER BY episode_number", (story_id,))
# # #     episodes = cursor.fetchall()
# # #     conn.close()

# # #     s3 = get_s3_client()

# # #     # Generate presigned URL for the main story cover
# # #     if story_data.get('cover_image'):
# # #         story_data['cover_image_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_CONFIG['bucket'], 'Key': story_data['cover_image']}, ExpiresIn=3600)

# # #     # Generate presigned URLs for each episode's audio and image
# # #     for episode in episodes:
# # #         if episode.get('audio_file'):
# # #             episode['audio_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_CONFIG['bucket'], 'Key': episode['audio_file']}, ExpiresIn=3600)
# # #         if episode.get('image_file'):
# # #             episode['image_url'] = s3.generate_presigned_url('get_object', Params={'Bucket': S3_CONFIG['bucket'], 'Key': episode['image_file']}, ExpiresIn=3600)

# # #     return render_template('story.html', story=story_data, episodes=episodes)



# # # # @app.route('/story/<int:story_id>')
# # # # def story(story_id):
# # # #     conn = get_db_connection()
# # # #     cur = conn.cursor(dictionary=True)
# # # #     cur.execute("SELECT story_id, story_name, author_name, description, cover_image, category FROM stories WHERE story_id = %s", (story_id,))
# # # #     story = cur.fetchone()
# # # #     if not story: abort(404)
# # # #     cur.execute("SELECT episode_number, title, audio_file, image_file FROM episodes WHERE story_id = %s ORDER BY episode_number", (story_id,))
# # # #     episodes = cur.fetchall()
# # # #     conn.close()
# # # #     return render_template('story.html', story=story, episodes=episodes)

# # # @app.route('/contact', methods=['GET', 'POST'])
# # # def contact():
# # #     if request.method == 'POST':
# # #         name    = request.form.get('name', '').strip()
# # #         email   = request.form.get('email', '').strip()
# # #         message = request.form.get('message', '').strip()
# # #         filename = 'static/contact-info.csv'
# # #         file_exists = exists(filename)
# # #         with open(filename, mode='a', newline='', encoding='utf-8') as file:
# # #             writer = csv.writer(file)
# # #             if not file_exists:
# # #                 writer.writerow(['Name', 'Email', 'Message'])
# # #             writer.writerow([name, email, message])
# # #         flash("Thanks for reaching out! We'll get back to you soon.", "success")
# # #         return "<h1>Thanks for reaching out! We'll get back to you soon.</h1>"
# # #     return render_template('contact.html')

# # # @app.route('/base')
# # # def base(): return render_template('base.html')
# # # @app.route('/about')
# # # def about(): return render_template('about.html')
# # # @app.route('/privacy')
# # # def privacy(): return render_template('privacy.html')
# # # @app.route('/termsAndCondition')
# # # def termsAndCondition(): return render_template('termsAndCondition.html')

# # # @app.errorhandler(404)
# # # def not_found(e): return render_template('error.html', message="Page not found."), 404
# # # @app.errorhandler(500)
# # # def server_error(e): return render_template('error.html', message="Internal server error."), 500

# # # if __name__ == '__main__':
# # #     app.run()
