from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector
import os
from werkzeug.utils import secure_filename
from decimal import Decimal
import cv2
from ultralytics import YOLO

app = Flask(__name__)
app.secret_key = 'your_secret_key'

UPLOAD_FOLDER = 'static/uploads'
RESULT_FOLDER = 'static/results'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['RESULT_FOLDER'] = RESULT_FOLDER

# Ensure upload and result folders exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)

# Load YOLO model
model = YOLO('runscopy/detect/waste_train/weights/best.pt')  # Adjust path as needed

# Cost table
cost_table = {
    "plastic": {0.5: 6, 1: 12, 2: 24},
    "metal": {0.5: 12, 1: 24, 2: 48},
    "paper": {0.5: 4, 1: 8, 2: 16}
}

# MySQL connection
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="12345",
    database="scrap_management"
)
cursor = db.cursor(dictionary=True)

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cursor.execute("SELECT * FROM users WHERE username = %s AND password = %s", (username, password))
        user = cursor.fetchone()

        if user:
            session['username'] = user['username']
            session['role'] = user['role']

            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user['role'] == 'dealer':
                return redirect(url_for('dealer_dashboard'))
            else:
                return redirect(url_for('user_dashboard'))
        else:
            return render_template('login.html', error="Invalid credentials")
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        contact = request.form.get('contact')
        location = request.form.get('location')
        role = request.form.get('role')

        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return render_template('signup.html', error="Username already exists")

        cursor.execute(
            "INSERT INTO users (username, password, role, contact, location) VALUES (%s, %s, %s, %s, %s)",
            (username, password, role, contact, location)
        )
        db.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if 'username' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        file = request.files['photo']
        item_type = request.form['item_type']
        weight = request.form['weight']
        description = request.form['description']

        cursor.execute("SELECT id FROM users WHERE username = %s", (session['username'],))
        user = cursor.fetchone()

        if user and file:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)

            cursor.execute(
                "INSERT INTO scrap_items (user_id, item_type, weight, description, photo) VALUES (%s, %s, %s, %s, %s)",
                (user['id'], item_type, weight, description, filepath)
            )
            db.commit()
            return redirect(url_for('user_dashboard'))
    return render_template('upload.html')

@app.route('/user')
def user_dashboard():
    if 'username' in session and session['role'] == 'user':
        # Get user ID
        cursor.execute("SELECT id FROM users WHERE username = %s", (session['username'],))
        user = cursor.fetchone()
        if not user:
            return "User not found in database", 404

        user_id = user['id']

        # Get all scrap items by user
        cursor.execute("""
            SELECT s.item_type, s.weight, s.status, s.created_at, s.photo,
                   d.username AS dealer_name, d.contact AS dealer_contact
            FROM scrap_items s
            LEFT JOIN users d ON s.dealer_id = d.id
            WHERE s.user_id = %s
        """, (user_id,))
        scrap_items = cursor.fetchall()

        # Get total weight of approved items
        cursor.execute("SELECT SUM(weight) AS total_weight FROM scrap_items WHERE user_id = %s AND status = 'approved'", (user_id,))
        result = cursor.fetchone()
        total_weight = float(result['total_weight'] or 0)

        # Calculate CO₂ saved (0.5 kg CO2 per 1 kg waste)
        total_co2_saved = round(total_weight * 0.5, 2)

        # Calculate reward points: 1 point per 20 kg of CO2 saved
        reward_points = int(total_co2_saved // 20)

        # Determine badge based on reward points
        if reward_points >= 20:
            badges = "Eco Warrior"
        elif reward_points >= 10:
            badges = "Eco Hero"
        elif reward_points >= 1:
            badges = "Eco Starter"
        else:
            badges = "No badge yet"

        return render_template('user_dashboard.html',
                               username=session['username'],
                               scrap_items=scrap_items,
                               total_weight=total_weight,
                               total_co2_saved=total_co2_saved,
                               reward_points=reward_points,
                               badges=badges)
    return redirect(url_for('login'))


@app.route('/admin')
def admin_dashboard():
    if 'username' in session and session['role'] == 'admin':
        cursor.execute("SELECT COUNT(*) AS sellers FROM users WHERE role = 'user'")
        sellers = cursor.fetchone()['sellers']

        cursor.execute("SELECT COUNT(*) AS dealers FROM users WHERE role = 'dealer'")
        dealers = cursor.fetchone()['dealers']

        cursor.execute("SELECT COUNT(*) AS today_listings FROM scrap_items WHERE DATE(created_at) = CURDATE()")
        today_listings = cursor.fetchone()['today_listings']

        cursor.execute("SELECT COUNT(*) AS pending_items FROM scrap_items WHERE status = 'pending'")
        pending_approval = cursor.fetchone()['pending_items']

        cursor.execute("SELECT COUNT(*) AS notifications FROM activities WHERE activity_type = 'notification' AND DATE(timestamp) = CURDATE()")
        notifications_sent = cursor.fetchone()['notifications']

        cursor.execute("SELECT COUNT(*) AS pickups FROM activities WHERE activity_type = 'pickup' AND DATE(timestamp) = CURDATE()")
        pickups_today = cursor.fetchone()['pickups']

        cursor.execute("""
            SELECT a.activity_type, a.details, a.timestamp, u.username
            FROM activities a
            JOIN users u ON a.user_id = u.id
            ORDER BY a.timestamp DESC
            LIMIT 10
        """)
        activities = cursor.fetchall()

        cursor.execute("""
            SELECT s.id, s.item_type, s.weight, s.status, s.user_id, u.username
            FROM scrap_items s
            JOIN users u ON s.user_id = u.id
            WHERE s.status = 'approved' AND s.dealer_id IS NULL
        """)
        approved_items = cursor.fetchall()

        cursor.execute("SELECT id, username FROM users WHERE role = 'dealer'")
        dealers_list = cursor.fetchall()

        return render_template('admin_dashboard.html',
                               username=session['username'],
                               sellers=sellers,
                               dealers=dealers,
                               today_listings=today_listings,
                               pending_approval=pending_approval,
                               notifications_sent=notifications_sent,
                               pickups_today=pickups_today,
                               activities=activities,
                               approved_items=approved_items,
                               dealers_list=dealers_list)
    return redirect(url_for('login'))

@app.route('/assign_dealer/<int:item_id>', methods=['POST'])
def assign_dealer(item_id):
    if 'username' in session and session['role'] == 'admin':
        dealer_id = request.form.get('dealer_id')
        cursor.execute("UPDATE scrap_items SET dealer_id = %s WHERE id = %s", (dealer_id, item_id))
        db.commit()
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login'))

@app.route('/upload_scrap', methods=['POST'])
def upload_scrap():
    if 'username' in session and session['role'] == 'user':
        item_type = request.form['item_type']
        weight = request.form['weight']
        description = request.form['description']

        cursor.execute("SELECT id FROM users WHERE username = %s", (session['username'],))
        user = cursor.fetchone()
        if not user:
            return "User not found", 404

        cursor.execute(
            "INSERT INTO scrap_items (user_id, item_type, weight, description, status) VALUES (%s, %s, %s, %s, 'pending')",
            (user['id'], item_type, weight, description)
        )
        db.commit()

        return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))

@app.route('/view_listings')
def view_listings():
    if 'username' in session and session['role'] == 'admin':
        cursor.execute("""
            SELECT scrap_items.id, scrap_items.item_type, scrap_items.weight,
                   scrap_items.description, scrap_items.status, scrap_items.created_at,
                   users.username
            FROM scrap_items
            JOIN users ON scrap_items.user_id = users.id
        """)
        listings = cursor.fetchall()
        return render_template('view_listings.html', listings=listings)
    return redirect(url_for('login'))

@app.route('/approve_scrap/<int:item_id>', methods=['POST'])
def approve_scrap(item_id):
    if 'username' in session and session['role'] == 'admin':
        cursor.execute("UPDATE scrap_items SET status = 'approved' WHERE id = %s", (item_id,))
        db.commit()
        return redirect(url_for('view_listings'))
    return redirect(url_for('login'))

@app.route('/reject_scrap/<int:item_id>', methods=['POST'])
def reject_scrap(item_id):
    if 'username' in session and session['role'] == 'admin':
        cursor.execute("UPDATE scrap_items SET status = 'pending' WHERE id = %s", (item_id,))
        db.commit()
        return redirect(url_for('view_listings'))
    return redirect(url_for('login'))

@app.route('/dealer', methods=['GET', 'POST'])
def dealer_dashboard():
    if 'username' in session and session['role'] == 'dealer':
        cursor.execute("SELECT id FROM users WHERE username = %s", (session['username'],))
        dealer = cursor.fetchone()
        if not dealer:
            return "Dealer not found", 404

        dealer_id = dealer['id']

        cursor.execute("""
            SELECT s.id, s.item_type, s.weight, s.status, s.created_at, u.username AS seller
            FROM scrap_items s
            JOIN users u ON s.user_id = u.id
            WHERE s.dealer_id = %s
        """, (dealer_id,))
        assigned_items = cursor.fetchall()

        prediction_result = None
        cost_summary = None
        result_image = None

        if request.method == 'POST':
            image = request.files['image']
            if image:
                filename = secure_filename(image.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                image.save(filepath)

                results = model.predict(source=filepath, save=False, conf=0.25)
                plotted = results[0].plot()
                result_path = os.path.join(app.config['RESULT_FOLDER'], filename)
                cv2.imwrite(result_path, plotted)

                class_names = model.names
                counts = {"plastic": 0, "metal": 0, "paper": 0}
                for box in results[0].boxes:
                    cls_id = int(box.cls[0].item())
                    name = class_names[cls_id].lower()
                    if name in counts:
                        counts[name] += 1

                cost_summary = {}
                for material in counts:
                    approx_kg = 0.5 * counts[material]
                    if approx_kg >= 2:
                        cost = cost_table[material][2]
                    elif approx_kg >= 1:
                        cost = cost_table[material][1]
                    elif approx_kg > 0:
                        cost = cost_table[material][0.5]
                    else:
                        cost = 0
                    cost_summary[material] = {
                        "count": counts[material],
                        "weight": approx_kg,
                        "cost": cost
                    }

                result_image = filename

        return render_template('dealer_dashboard.html',
                               username=session['username'],
                               assigned_items=assigned_items,
                               result_image=result_image,
                               cost_summary=cost_summary)
    return redirect(url_for('login'))

@app.route('/redeem_center')
def redeem_center():
    if 'username' not in session or session['role'] != 'user':
        return redirect(url_for('login'))

    # Get user ID
    cursor.execute("SELECT id FROM users WHERE username = %s", (session['username'],))
    user = cursor.fetchone()
    if not user:
        return "User not found", 404
    user_id = user['id']

    # Calculate total weight of approved items
    cursor.execute("SELECT SUM(weight) AS total_weight FROM scrap_items WHERE user_id = %s AND status = 'approved'", (user_id,))
    result = cursor.fetchone()
    total_weight = float(result['total_weight'] or 0)
    
    # Calculate CO2 saved and reward points
    total_co2_saved = round(total_weight * 0.5, 2)
    reward_points = int(total_co2_saved // 20)

    # Sample plant options
    plants = [
        {"name": "Aloe Vera", "tokens": 10, "size": "Small"},
        {"name": "Areca Palm", "tokens": 20, "size": "Medium"},
        {"name": "Money Plant", "tokens": 10, "size": "Small"},
        {"name": "Rubber Plant", "tokens": 30, "size": "Large"},
        {"name": "Snake Plant", "tokens": 15, "size": "Medium"},
    ]

    return render_template('redeem_center.html',
                           reward_points=reward_points,
                           plants=plants)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
