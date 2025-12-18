from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import json
import os
from datetime import datetime
import openpyxl
from werkzeug.utils import secure_filename
from collections import defaultdict
import threading
import time
from difflib import SequenceMatcher

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'voting-game-secret')
app.config['UPLOAD_FOLDER'] = 'uploads'

CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25, logger=True, engineio_logger=True)

os.makedirs('uploads', exist_ok=True)

DATABASE_FILE = 'database.json'

def fuzzy_match(name1, name2, threshold=0.85):
    name1_clean = name1.lower().strip()
    name2_clean = name2.lower().strip()
    
    if name1_clean == name2_clean:
        return True
    
    ratio = SequenceMatcher(None, name1_clean, name2_clean).ratio()
    if ratio >= threshold:
        return True
    
    name1_parts = name1_clean.split()
    name2_parts = name2_clean.split()
    
    shorter_parts = name1_parts if len(name1_parts) < len(name2_parts) else name2_parts
    longer_parts = name2_parts if len(name1_parts) < len(name2_parts) else name1_parts
    
    matches = 0
    for short_part in shorter_parts:
        for long_part in longer_parts:
            if short_part == long_part or (len(short_part) > 0 and len(long_part) > 0 and short_part[0] == long_part[0]):
                matches += 1
                break
    
    if matches >= len(shorter_parts) * 0.7:
        return True
    
    return False

def init_database():
    if not os.path.exists(DATABASE_FILE):
        initial_data = {
            "teams": [
                {"id": 1, "name": "Engineering"},
                {"id": 2, "name": "IT"},
                {"id": 3, "name": "PLM"},
                {"id": 4, "name": "HR"},
                {"id": 5, "name": "Sales"}
            ],
            "members": [
                {"id": 1, "name": "Alice", "team_id": 1, "team_name": "Engineering"},
                {"id": 2, "name": "Bob", "team_id": 1, "team_name": "Engineering"},
                {"id": 3, "name": "Charlie", "team_id": 1, "team_name": "Engineering"},
                {"id": 4, "name": "Dinesh Kannan", "team_id": 1, "team_name": "Engineering"},
                {"id": 5, "name": "Emma", "team_id": 2, "team_name": "IT"},
                {"id": 6, "name": "Frank", "team_id": 2, "team_name": "IT"},
                {"id": 7, "name": "Henry", "team_id": 3, "team_name": "PLM"},
                {"id": 8, "name": "Iris", "team_id": 3, "team_name": "PLM"},
                {"id": 9, "name": "Karen", "team_id": 4, "team_name": "HR"},
                {"id": 10, "name": "Leo", "team_id": 4, "team_name": "HR"},
                {"id": 11, "name": "Monica", "team_id": 5, "team_name": "Sales"},
                {"id": 12, "name": "Nathan", "team_id": 5, "team_name": "Sales"}
            ],
            "questions": [
                {"id": 1, "text": "Who is the funniest person in the office?", "active": False},
                {"id": 2, "text": "Who drinks the most coffee?", "active": False},
                {"id": 3, "text": "Who is always late to meetings?", "active": False},
                {"id": 4, "text": "Who has the best sense of humor?", "active": False},
                {"id": 5, "text": "Who is the most helpful colleague?", "active": False}
            ],
            "votes": [],
            "current_question": None,
            "timer": {"duration": 30, "remaining": 30, "active": False}
        }
        save_database(initial_data)

def load_database():
    try:
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    except:
        init_database()
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)

def save_database(data):
    with open(DATABASE_FILE, 'w') as f:
        json.dump(data, f, indent=2)

init_database()

timer_thread = None
timer_active = False
timer_lock = threading.Lock()

def timer_worker():
    global timer_active
    print("🔥 Timer thread started!")
    
    while timer_active:
        with timer_lock:
            db = load_database()
            
            if not timer_active or db['timer']['remaining'] <= 0:
                break
            
            db['timer']['remaining'] -= 1
            remaining = db['timer']['remaining']
            save_database(db)
        
        socketio.emit('timer_update', {
            'remaining': remaining,
            'minutes': remaining // 60,
            'seconds': remaining % 60
        }, namespace='/')
        
        print(f"⏱️ Timer: {remaining}s")
        
        if remaining <= 0:
            timer_active = False
            socketio.emit('timer_finished', {}, namespace='/')
            
            db = load_database()
            leaderboard = calculate_leaderboard(db)
            socketio.emit('show_results', {'winners': leaderboard}, namespace='/')
            print(f"🏆 Results sent!")
            break
        
        time.sleep(1)
    
    print("🛑 Timer stopped!")

@app.route('/')
def index():
    return send_from_directory('frontend', 'login.html')

@app.route('/<path:path>')
def serve_frontend(path):
    return send_from_directory('frontend', path)

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'success': False, 'message': 'Name is required'}), 400
        
        db = load_database()
        
        matched_member = None
        best_match_ratio = 0
        
        for member in db['members']:
            if fuzzy_match(name, member['name']):
                ratio = SequenceMatcher(None, name.lower(), member['name'].lower()).ratio()
                if ratio > best_match_ratio:
                    best_match_ratio = ratio
                    matched_member = member
        
        if matched_member:
            return jsonify({
                'success': True,
                'userId': matched_member['id'],
                'userName': matched_member['name'],
                'userTeam': matched_member['team_name'],
                'userTeamId': matched_member['team_id']
            })
        else:
            return jsonify({
                'success': False,
                'message': f'No match found for "{name}"'
            }), 404
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/vote', methods=['POST'])
def submit_vote():
    try:
        data = request.json
        db = load_database()
        
        if not db['current_question']:
            return jsonify({'success': False, 'message': 'No active question'}), 400
        
        voter_id = data.get('voterId')
        question_id = db['current_question']['id']
        
        existing_vote = next((v for v in db['votes'] if v['question_id'] == question_id and v['voter_id'] == voter_id), None)
        
        if existing_vote:
            return jsonify({'success': False, 'message': 'Already voted'}), 400
        
        vote = {
            'id': len(db['votes']) + 1,
            'question_id': question_id,
            'voter_id': voter_id,
            'voter_name': data.get('voterName'),
            'votee_name': data.get('voteeName'),
            'team_id': data.get('teamId'),
            'timestamp': datetime.now().isoformat()
        }
        
        db['votes'].append(vote)
        save_database(db)
        
        leaderboard = calculate_leaderboard(db)
        socketio.emit('leaderboard_update', {'winners': leaderboard}, namespace='/')
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Vote error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/members', methods=['GET'])
def get_members():
    try:
        db = load_database()
        team_id = request.args.get('team_id', type=int)
        exclude_id = request.args.get('exclude_id', type=int)
        
        members = db['members']
        
        if team_id:
            members = [m for m in members if m['team_id'] == team_id]
        
        if exclude_id:
            members = [m for m in members if m['id'] != exclude_id]
        
        return jsonify(members)
    except Exception as e:
        print(f"Get members error: {e}")
        return jsonify([]), 500

@app.route('/api/members', methods=['POST'])
def add_member():
    try:
        data = request.json
        db = load_database()
        
        if next((m for m in db['members'] if m['name'].lower() == data['name'].lower()), None):
            return jsonify({'success': False, 'message': 'Member exists'}), 400
        
        team = next((t for t in db['teams'] if t['name'] == data['team_name']), None)
        if not team:
            return jsonify({'success': False, 'message': 'Team not found'}), 404
        
        member = {
            'id': max([m['id'] for m in db['members']], default=0) + 1,
            'name': data['name'],
            'team_id': team['id'],
            'team_name': team['name']
        }
        
        db['members'].append(member)
        save_database(db)
        
        return jsonify({'success': True, 'member': member})
    except Exception as e:
        print(f"Add member error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/teams', methods=['GET'])
def get_teams():
    try:
        db = load_database()
        return jsonify(db['teams'])
    except Exception as e:
        print(f"Get teams error: {e}")
        return jsonify([]), 500

@app.route('/api/teams', methods=['POST'])
def add_team():
    try:
        data = request.json
        db = load_database()
        
        if next((t for t in db['teams'] if t['name'].lower() == data['name'].lower()), None):
            return jsonify({'success': False, 'message': 'Team exists'}), 400
        
        team = {
            'id': max([t['id'] for t in db['teams']], default=0) + 1,
            'name': data['name']
        }
        
        db['teams'].append(team)
        save_database(db)
        
        return jsonify({'success': True, 'team': team})
    except Exception as e:
        print(f"Add team error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/questions', methods=['GET'])
def get_questions():
    try:
        db = load_database()
        return jsonify(db['questions'])
    except Exception as e:
        print(f"Get questions error: {e}")
        return jsonify([]), 500

@app.route('/api/questions', methods=['POST'])
def add_question():
    try:
        data = request.json
        db = load_database()
        
        question = {
            'id': max([q['id'] for q in db['questions']], default=0) + 1,
            'text': data['text'],
            'active': False
        }
        
        db['questions'].append(question)
        save_database(db)
        
        return jsonify({'success': True, 'question': question})
    except Exception as e:
        print(f"Add question error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/admin/broadcast', methods=['POST'])
def broadcast_question():
    global timer_thread, timer_active
    
    try:
        data = request.json
        db = load_database()
        
        question_text = data.get('question')
        question = next((q for q in db['questions'] if q['text'] == question_text), None)
        
        if not question:
            return jsonify({'success': False, 'message': 'Question not found'}), 404
        
        timer_active = False
        if timer_thread and timer_thread.is_alive():
            timer_thread.join(timeout=2)
        
        duration = db['timer']['duration']
        
        db['current_question'] = question
        db['timer']['remaining'] = duration
        db['timer']['active'] = True
        
        for q in db['questions']:
            q['active'] = q['id'] == question['id']
        
        db['votes'] = [v for v in db['votes'] if v['question_id'] != question['id']]
        
        save_database(db)
        
        timer_active = True
        timer_thread = threading.Thread(target=timer_worker, daemon=True)
        timer_thread.start()
        
        socketio.emit('question_broadcast', {
            'question': question['text'],
            'questionId': question['id'],
            'duration': duration
        }, namespace='/')
        
        print(f"📢 Broadcast: {question['text']}")
        
        return jsonify({'success': True, 'question': question})
    except Exception as e:
        print(f"Broadcast error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

def calculate_leaderboard(db):
    if not db['current_question']:
        return []
    
    votes = [v for v in db['votes'] if v['question_id'] == db['current_question']['id']]
    counts = defaultdict(int)
    info = {}
    
    for v in votes:
        counts[v['votee_name']] += 1
        if v['votee_name'] not in info:
            m = next((m for m in db['members'] if m['name'] == v['votee_name']), None)
            if m:
                info[v['votee_name']] = {'team_id': m['team_id'], 'team_name': m['team_name']}
    
    winners = {}
    for name, votes_count in counts.items():
        if name in info:
            tid = info[name]['team_id']
            if tid not in winners or votes_count > winners[tid]['votes']:
                winners[tid] = {'team_id': tid, 'team': info[name]['team_name'], 'name': name, 'votes': votes_count}
    
    result = list(winners.values())
    result.sort(key=lambda x: x['votes'], reverse=True)
    for i, w in enumerate(result):
        w['rank'] = i + 1
    return result

@socketio.on('connect')
def handle_connect():
    print('✅ Client connected')
    emit('connected', {'message': 'Connected'})

@socketio.on('disconnect')
def handle_disconnect():
    print('❌ Client disconnected')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("🎮 OFFICE LEGENDS STARTING...")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
