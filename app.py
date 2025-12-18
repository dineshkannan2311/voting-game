from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import os
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from werkzeug.utils import secure_filename
import openpyxl
import threading
import time

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
CORS(app)

os.makedirs('uploads', exist_ok=True)

DATABASE_FILE = 'database.json'

# Global timer state
timer_state = {
    'active': False,
    'remaining': 0,
    'started_at': None
}

def fuzzy_match(name1, name2, threshold=0.85):
    """Fuzzy match names with 85% threshold"""
    name1_clean = name1.lower().strip()
    name2_clean = name2.lower().strip()
    
    if name1_clean == name2_clean:
        return True
    
    ratio = SequenceMatcher(None, name1_clean, name2_clean).ratio()
    if ratio >= threshold:
        return True
    
    name1_parts = name1_clean.split()
    name2_parts = name2_clean.split()
    
    shorter = name1_parts if len(name1_parts) < len(name2_parts) else name2_parts
    longer = name2_parts if len(name1_parts) < len(name2_parts) else name1_parts
    
    matches = sum(1 for s in shorter if any(s == l or (s and l and s[0] == l[0]) for l in longer))
    
    return matches >= len(shorter) * 0.7

def init_database():
    if not os.path.exists(DATABASE_FILE):
        data = {
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
                {"id": 6, "name": "Frank", "team_id": 2, "team_name": "IT"}
            ],
            "questions": [
                {"id": 1, "text": "Who is the funniest person in the office?"},
                {"id": 2, "text": "Who drinks the most coffee?"},
                {"id": 3, "text": "Who is always late to meetings?"}
            ],
            "votes": [],
            "current_question": None,
            "timer_duration": 30
        }
        save_database(data)

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

# Routes
@app.route('/')
def index():
    return send_from_directory('frontend', 'login.html')

@app.route('/<path:path>')
def serve_file(path):
    return send_from_directory('frontend', path)

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'success': False, 'message': 'Name required'}), 400
        
        db = load_database()
        matched = None
        best_ratio = 0
        
        for member in db['members']:
            if fuzzy_match(name, member['name']):
                ratio = SequenceMatcher(None, name.lower(), member['name'].lower()).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    matched = member
        
        if matched:
            return jsonify({
                'success': True,
                'userId': matched['id'],
                'userName': matched['name'],
                'userTeam': matched['team_name'],
                'userTeamId': matched['team_id']
            })
        
        return jsonify({'success': False, 'message': f'No match for "{name}"'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/current-state', methods=['GET'])
def get_current_state():
    """Single endpoint for all current state - POLLING ENDPOINT"""
    try:
        db = load_database()
        
        # Calculate remaining time
        remaining = 0
        if timer_state['active'] and timer_state['started_at']:
            elapsed = (datetime.now() - timer_state['started_at']).total_seconds()
            remaining = max(0, timer_state['remaining'] - int(elapsed))
            
            if remaining <= 0:
                timer_state['active'] = False
        
        response = {
            'current_question': db['current_question'],
            'timer_active': timer_state['active'],
            'timer_remaining': int(remaining),
            'show_results': not timer_state['active'] and db['current_question'] is not None
        }
        
        # If showing results, include leaderboard
        if response['show_results']:
            response['results'] = calculate_leaderboard(db)
        
        return jsonify(response)
    except Exception as e:
        print(f"Error in current_state: {e}")
        return jsonify({'current_question': None, 'timer_active': False, 'timer_remaining': 0}), 500

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
        return jsonify([]), 500

@app.route('/api/vote', methods=['POST'])
def vote():
    try:
        data = request.json
        db = load_database()
        
        if not db['current_question']:
            return jsonify({'success': False, 'message': 'No active question'}), 400
        
        voter_id = data.get('voterId')
        question_id = db['current_question']['id']
        
        # Check if already voted
        if any(v['question_id'] == question_id and v['voter_id'] == voter_id for v in db['votes']):
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
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/teams', methods=['GET'])
def get_teams():
    try:
        return jsonify(load_database()['teams'])
    except:
        return jsonify([]), 500

@app.route('/api/teams', methods=['POST'])
def add_team():
    try:
        data = request.json
        db = load_database()
        
        if any(t['name'].lower() == data['name'].lower() for t in db['teams']):
            return jsonify({'success': False, 'message': 'Team exists'}), 400
        
        team = {
            'id': max([t['id'] for t in db['teams']], default=0) + 1,
            'name': data['name']
        }
        db['teams'].append(team)
        save_database(db)
        
        return jsonify({'success': True, 'team': team})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/members', methods=['POST'])
def add_member():
    try:
        data = request.json
        db = load_database()
        
        if any(m['name'].lower() == data['name'].lower() for m in db['members']):
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
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/questions', methods=['GET'])
def get_questions():
    try:
        return jsonify(load_database()['questions'])
    except:
        return jsonify([]), 500

@app.route('/api/questions', methods=['POST'])
def add_question():
    try:
        data = request.json
        db = load_database()
        
        question = {
            'id': max([q['id'] for q in db['questions']], default=0) + 1,
            'text': data['text']
        }
        db['questions'].append(question)
        save_database(db)
        
        return jsonify({'success': True, 'question': question})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/admin/broadcast', methods=['POST'])
def broadcast():
    try:
        data = request.json
        db = load_database()
        
        question_text = data.get('question')
        question = next((q for q in db['questions'] if q['text'] == question_text), None)
        
        if not question:
            return jsonify({'success': False, 'message': 'Question not found'}), 404
        
        # Set current question
        db['current_question'] = question
        
        # Clear old votes for this question
        db['votes'] = [v for v in db['votes'] if v['question_id'] != question['id']]
        
        save_database(db)
        
        # Start timer
        timer_state['active'] = True
        timer_state['remaining'] = db.get('timer_duration', 30)
        timer_state['started_at'] = datetime.now()
        
        print(f"📢 Broadcast: {question['text']} - Timer: {timer_state['remaining']}s")
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/admin/clear', methods=['POST'])
def clear_question():
    try:
        db = load_database()
        db['current_question'] = None
        save_database(db)
        
        timer_state['active'] = False
        timer_state['remaining'] = 0
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/admin/timer', methods=['POST'])
def set_timer():
    try:
        data = request.json
        seconds = data.get('seconds', 30)
        
        db = load_database()
        db['timer_duration'] = seconds
        save_database(db)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/admin/members/upload', methods=['POST'])
def upload_excel():
    """Upload Excel file with members"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400
        
        file = request.files['file']
        
        if not file.filename:
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            return jsonify({'success': False, 'message': 'Only Excel files (.xlsx, .xls) allowed'}), 400
        
        # Save file temporarily
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Read Excel
        workbook = openpyxl.load_workbook(filepath)
        sheet = workbook.active
        
        db = load_database()
        added = 0
        
        # Process rows (skip header if exists)
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not row or len(row) < 2:
                continue
            
            name = str(row[0]).strip() if row[0] else None
            team_name = str(row[1]).strip() if row[1] else None
            
            if not name or not team_name:
                continue
            
            # Check if member already exists
            if any(m['name'].lower() == name.lower() for m in db['members']):
                continue
            
            # Find or create team
            team = next((t for t in db['teams'] if t['name'].lower() == team_name.lower()), None)
            
            if not team:
                # Create new team
                team = {
                    'id': max([t['id'] for t in db['teams']], default=0) + 1,
                    'name': team_name
                }
                db['teams'].append(team)
            
            # Add member
            member = {
                'id': max([m['id'] for m in db['members']], default=0) + 1,
                'name': name,
                'team_id': team['id'],
                'team_name': team['name']
            }
            
            db['members'].append(member)
            added += 1
        
        save_database(db)
        
        # Delete temp file
        try:
            os.remove(filepath)
        except:
            pass
        
        return jsonify({'success': True, 'count': added, 'message': f'Added {added} members'})
        
    except Exception as e:
        print(f"Excel upload error: {e}")
        return jsonify({'success': False, 'message': f'Upload failed: {str(e)}'}), 500

def calculate_leaderboard(db):
    if not db['current_question']:
        return []
    
    votes = [v for v in db['votes'] if v['question_id'] == db['current_question']['id']]
    
    from collections import defaultdict
    counts = defaultdict(int)
    info = {}
    
    for v in votes:
        counts[v['votee_name']] += 1
        if v['votee_name'] not in info:
            m = next((m for m in db['members'] if m['name'] == v['votee_name']), None)
            if m:
                info[v['votee_name']] = {'team_id': m['team_id'], 'team_name': m['team_name']}
    
    winners = {}
    for name, vote_count in counts.items():
        if name in info:
            tid = info[name]['team_id']
            if tid not in winners or vote_count > winners[tid]['votes']:
                winners[tid] = {
                    'team_id': tid,
                    'team': info[name]['team_name'],
                    'name': name,
                    'votes': vote_count
                }
    
    result = list(winners.values())
    result.sort(key=lambda x: x['votes'], reverse=True)
    
    for i, w in enumerate(result):
        w['rank'] = i + 1
    
    return result

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("🎮 OFFICE LEGENDS - POLLING VERSION")
    print(f"✅ Server running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
