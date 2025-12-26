import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from models import db, User, Match, calculate_elo_change # Import our new tools
import requests
import uuid
import time
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cyberpunk_secret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///dueler.db' # Simple file DB
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
socketio = SocketIO(app, async_mode='eventlet')

# Create DB tables if not exist
with app.app_context():
    db.create_all()

CPP_ENGINE_URL = "http://127.0.0.1:5001/get-duel"
lobbies = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create', methods=['POST'])
def create():
    room_id = str(uuid.uuid4())[:8]
    lobbies[room_id] = {
        'mode': request.form.get('mode'),
        'rating': int(request.form.get('rating') or 1200),
        'duration': int(request.form.get('duration') or 30),
        'players': {}, 
        'state': 'waiting',
        'start_time': 0,
        'scores': {},
        'problem_status': {} # NEW: Tracks who claimed which problem (Lockout)
    }
    return jsonify({'room_id': room_id})

@app.route('/lobby/<room_id>')
def lobby(room_id):
    if room_id not in lobbies: return "Lobby not found", 404
    lobby_data = lobbies[room_id]
    return render_template('lobby.html', room_id=room_id, mode=lobby_data['mode'], duration=lobby_data['duration'])

# ... (Keep your existing get_banned_problems function here) ...

# --- WEBSOCKETS ---

@app.route('/user_stats/<handle>')
def user_stats(handle):
    user = User.query.filter_by(handle=handle).first()
    if not user: return jsonify({'rating': 1500, 'matches': 0})
    return jsonify({'rating': user.rating, 'matches': user.matches_played})

@socketio.on('join')
def on_join(data):
    room = data['room']
    handle = data['handle']
    join_room(room)
    
    if room in lobbies:
        # Create/Load User in DB
        user = User.query.filter_by(handle=handle).first()
        if not user:
            user = User(handle=handle)
            db.session.add(user)
            db.session.commit()

        if handle not in lobbies[room]['players']:
            lobbies[room]['players'][handle] = {
                'handle': handle, 
                'ready': False, 
                'rating': user.rating # Fetch internal rating
            }
            lobbies[room]['scores'][handle] = {'solved': [], 'penalty': 0}
        
        emit('update_players', list(lobbies[room]['players'].values()), room=room)
        emit('update_leaderboard', lobbies[room]['scores'], room=room)

@socketio.on('toggle_ready')
def on_ready(data):
    room = data['room']
    handle = data['handle']
    if room in lobbies:
        lobbies[room]['players'][handle]['ready'] = not lobbies[room]['players'][handle]['ready']
        emit('update_players', list(lobbies[room]['players'].values()), room=room)

@socketio.on('start_game')
def on_start(data):
    room = data['room']
    lobby = lobbies.get(room)
    if not lobby: return

    # ... (Keep existing Ready Checks and Fetch Logic) ...
    # ... INSIDE SUCCESS BLOCK: ...
            
            lobby['state'] = 'running'
            lobby['start_time'] = time.time()
            lobby['problem_status'] = {p['id']: None for p in game_data['problems']} # Initialize Lockout
            
            game_data['track_ids'] = [p['id'] for p in game_data['problems']]
            game_data['end_time'] = lobby['start_time'] + (lobby['duration'] * 60)
            
            emit('game_started', game_data, room=room)


@socketio.on('report_solve')
def on_report_solve(data):
    room = data['room']
    handle = data['winner']
    pid = data['problem_id']
    
    lobby = lobbies.get(room)
    if not lobby or lobby['state'] != 'running': return

    # 1. LOCKOUT CHECK (The "Borrow from TLE Bot" feature)
    if lobby['mode'] == 'lockout':
        # If someone else already claimed this problem, ignore this report
        if lobby['problem_status'].get(pid) is not None:
            return 

    # 2. Verify with CF API (Keep your existing verify logic here)
    # ... (Assume Verified) ...
    
    # 3. Update State
    if lobby['mode'] == 'lockout':
        lobby['problem_status'][pid] = handle # CLAIM IT
        emit('problem_locked', {'id': pid, 'winner': handle}, room=room) # Notify frontend to gray it out
    
    # Update Scores
    score_data = lobby['scores'][handle]
    if pid not in score_data['solved']:
        score_data['solved'].append(pid)
        score_data['penalty'] += int((time.time() - lobby['start_time']) / 60)
        
        emit('update_leaderboard', lobby['scores'], room=room)
        emit('notification', f"{handle} solved {pid}!", room=room)

    # 4. End Condition Logic
    # If 1v1 Classic -> End Game + Update ELO
    if lobby['mode'] == 'classic':
        handle_game_end(room, winner=handle)

def handle_game_end(room, winner):
    lobby = lobbies[room]
    lobby['state'] = 'ended'
    
    players = list(lobby['players'].keys())
    loser = players[0] if players[0] != winner else players[1]
    
    # DB Update: Calculate ELO
    w_user = User.query.filter_by(handle=winner).first()
    l_user = User.query.filter_by(handle=loser).first()
    
    if w_user and l_user:
        change = calculate_elo_change(w_user.rating, l_user.rating)
        w_user.rating += change
        l_user.rating -= change
        w_user.matches_played += 1
        l_user.matches_played += 1
        
        match = Match(winner_handle=winner, loser_handle=loser, mode=lobby['mode'], rating_change=change)
        db.session.add(match)
        db.session.commit()
        
        emit('game_over', {
            'winner': winner, 
            'problem': 'Classic Duel', 
            'new_ratings': {winner: w_user.rating, loser: l_user.rating},
            'change': change
        }, room=room)

if __name__ == '__main__':
    socketio.run(app, port=8080, debug=True)