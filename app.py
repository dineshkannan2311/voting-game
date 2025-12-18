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

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'voting-game-secret')
app.config['UPLOAD_FOLDER'] = 'uploads'

CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

os.makedirs('uploads', exist_ok=True)

DATABASE_FILE = 'database.json'

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
                {"id": 4, "name": "Dinesh", "team_id": 1, "team_name": "Engineering"},
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
    with open(DATABASE_FILE, 'r') as f:
        return json.load(f)

def save_database(data):
    with open(DATABASE_FILE, 'w') as f:
        json.dump(data, f, indent=2)

init_database()

timer_thread = None
timer_active = False

def timer_worker():
    global timer_active
    db = load_database()
    while timer_active and db['timer']['remaining'] > 0:
        time.sleep(1)
        db = load_database()
        db['timer']['remaining'] -= 1
        save_database(db)
        socketio.emit('timer_update', {'remaining': db['timer']['remaining'], 'minutes': db['timer']['remaining'] // 60, 'seconds': db['timer']['remaining'] % 60}, namespace='/')
    if db['timer']['remaining'] <= 0:
        timer_active = False
        socketio.emit('timer_finished', {}, namespace='/')
        leaderboard = calculate_leaderboard(db)
        socketio.emit('show_results', {'winners': leaderboard}, namespace='/')

@app.route('/')
def index():
    return send_from_directory('frontend', 'login.html')

@app.route('/<path:path>')
def serve_frontend(path):
    return send_from_directory('frontend', path)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Name is required'}), 400
    db = load_database()
    member = next((m for m in db['members'] if m['name'].lower() == name.lower()), None)
    if member:
        return jsonify({'success': True, 'userId': member['id'], 'userName': member['name'], 'userTeam': member['team_name'], 'userTeamId': member['team_id']})
    else:
        return jsonify({'success': False, 'message': f'Member "{name}" not found'}), 404

@app.route('/api/vote', methods=['POST'])
def submit_vote():
    data = request.json
    db = load_database()
    if not db['current_question']:
        return jsonify({'success': False, 'message': 'No active question'}), 400
    vote = {'id': len(db['votes']) + 1, 'question_id': db['current_question']['id'], 'voter_id': data.get('voterId'), 'voter_name': data.get('voterName'), 'votee_name': data.get('voteeName'), 'team_id': data.get('teamId'), 'timestamp': datetime.now().isoformat()}
    db['votes'].append(vote)
    save_database(db)
    leaderboard = calculate_leaderboard(db)
    socketio.emit('leaderboard_update', {'winners': leaderboard}, namespace='/')
    return jsonify({'success': True})

@app.route('/api/members', methods=['GET'])
def get_members():
    db = load_database()
    team_id = request.args.get('team_id', type=int)
    return jsonify([m for m in db['members'] if m['team_id'] == team_id] if team_id else db['members'])

@app.route('/api/members', methods=['POST'])
def add_member():
    data = request.json
    db = load_database()
    if next((m for m in db['members'] if m['name'].lower() == data['name'].lower()), None):
        return jsonify({'success': False, 'message': 'Member already exists'}), 400
    team = next((t for t in db['teams'] if t['name'] == data['team_name']), None)
    if not team:
        return jsonify({'success': False, 'message': 'Team not found'}), 404
    member = {'id': max([m['id'] for m in db['members']], default=0) + 1, 'name': data['name'], 'team_id': team['id'], 'team_name': team['name']}
    db['members'].append(member)
    save_database(db)
    return jsonify({'success': True, 'member': member})

@app.route('/api/members/<int:member_id>', methods=['DELETE'])
def delete_member(member_id):
    db = load_database()
    db['members'] = [m for m in db['members'] if m['id'] != member_id]
    save_database(db)
    return jsonify({'success': True})

@app.route('/api/admin/members/upload', methods=['POST'])
def upload_excel():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file'}), 400
    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'success': False, 'message': 'Invalid format'}), 400
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
        file.save(filepath)
        workbook = openpyxl.load_workbook(filepath)
        sheet = workbook.active
        db = load_database()
        added = 0
        for row in sheet.iter_rows(min_row=1, values_only=True):
            if len(row) >= 2 and row[0] and row[1]:
                name, team_name = str(row[0]).strip(), str(row[1]).strip()
                if not next((m for m in db['members'] if m['name'].lower() == name.lower()), None):
                    team = next((t for t in db['teams'] if t['name'].lower() == team_name.lower()), None)
                    if not team:
                        team = {'id': max([t['id'] for t in db['teams']], default=0) + 1, 'name': team_name}
                        db['teams'].append(team)
                    db['members'].append({'id': max([m['id'] for m in db['members']], default=0) + 1, 'name': name, 'team_id': team['id'], 'team_name': team['name']})
                    added += 1
        save_database(db)
        os.remove(filepath)
        return jsonify({'success': True, 'count': added})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/teams', methods=['GET'])
def get_teams():
    return jsonify(load_database()['teams'])

@app.route('/api/teams', methods=['POST'])
def add_team():
    data = request.json
    db = load_database()
    if next((t for t in db['teams'] if t['name'].lower() == data['name'].lower()), None):
        return jsonify({'success': False, 'message': 'Team exists'}), 400
    team = {'id': max([t['id'] for t in db['teams']], default=0) + 1, 'name': data['name']}
    db['teams'].append(team)
    save_database(db)
    return jsonify({'success': True, 'team': team})

@app.route('/api/teams/<int:team_id>', methods=['DELETE'])
def delete_team(team_id):
    db = load_database()
    db['teams'] = [t for t in db['teams'] if t['id'] != team_id]
    db['members'] = [m for m in db['members'] if m['team_id'] != team_id]
    save_database(db)
    return jsonify({'success': True})

@app.route('/api/questions', methods=['GET'])
def get_questions():
    return jsonify(load_database()['questions'])

@app.route('/api/questions', methods=['POST'])
def add_question():
    data = request.json
    db = load_database()
    question = {'id': max([q['id'] for q in db['questions']], default=0) + 1, 'text': data['text'], 'active': False}
    db['questions'].append(question)
    save_database(db)
    return jsonify({'success': True, 'question': question})

@app.route('/api/questions/<int:question_id>', methods=['DELETE'])
def delete_question(question_id):
    db = load_database()
    db['questions'] = [q for q in db['questions'] if q['id'] != question_id]
    save_database(db)
    return jsonify({'success': True})

@app.route('/api/admin/broadcast', methods=['POST'])
def broadcast_question():
    global timer_thread, timer_active
    data = request.json
    db = load_database()
    question = next((q for q in db['questions'] if q['text'] == data.get('question')), None)
    if not question:
        return jsonify({'success': False, 'message': 'Question not found'}), 404
    duration = db['timer']['duration']
    db['current_question'] = question
    db['timer']['remaining'] = duration
    db['timer']['active'] = True
    for q in db['questions']:
        q['active'] = q['id'] == question['id']
    db['votes'] = [v for v in db['votes'] if v['question_id'] != question['id']]
    save_database(db)
    timer_active = True
    if timer_thread is None or not timer_thread.is_alive():
        timer_thread = threading.Thread(target=timer_worker)
        timer_thread.daemon = True
        timer_thread.start()
    socketio.emit('question_broadcast', {'question': question['text'], 'questionId': question['id'], 'duration': duration}, namespace='/')
    return jsonify({'success': True, 'question': question})

@app.route('/api/admin/clear', methods=['POST'])
def clear_question():
    global timer_active
    timer_active = False
    db = load_database()
    db['current_question'] = None
    db['timer']['active'] = False
    for q in db['questions']:
        q['active'] = False
    save_database(db)
    socketio.emit('question_cleared', {}, namespace='/')
    return jsonify({'success': True})

@app.route('/api/admin/timer/set', methods=['POST'])
def set_timer():
    seconds = request.json.get('seconds', 30)
    db = load_database()
    db['timer']['duration'] = seconds
    db['timer']['remaining'] = seconds
    save_database(db)
    return jsonify({'success': True, 'duration': seconds})

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

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    db = load_database()
    return jsonify({'winners': calculate_leaderboard(db), 'question': db['current_question']})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    db = load_database()
    votes = len([v for v in db['votes'] if db['current_question'] and v['question_id'] == db['current_question']['id']])
    return jsonify({'total_teams': len(db['teams']), 'total_members': len(db['members']), 'total_questions': len(db['questions']), 'total_votes': votes, 'timer_duration': db['timer']['duration']})

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
