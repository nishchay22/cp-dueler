import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from models import db, User, Match, calculate_elo_change
import requests
import uuid
import time
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cyberpunk_secret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///dueler.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
socketio = SocketIO(app, async_mode='eventlet')

# Create DB tables if not exist
with app.app_context():
    db.create_all()

# Ensure C++ Engine URL is correct (Internal Localhost)
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
        'problem_status': {} # Tracks claims for Lockout mode
    }
    return jsonify({'room_id': room_id})

@app.route('/lobby/<room_id>')
def lobby(room_id):
    if room_id not in lobbies: return "Lobby not found", 404
    lobby_data = lobbies[room_id]
    return render_template('lobby.html', room_id=room_id, mode=lobby_data['mode'], duration=lobby_data['duration'])

@app.route('/user_stats/<handle>')
def user_stats(handle):
    user = User.query.filter_by(handle=handle).first()
    if not user: return jsonify({'rating': 1500, 'matches': 0})
    return jsonify({'rating': user.rating, 'matches': user.matches_played})

# --- HELPER: ROBUST CF API FETCH ---
def get_banned_problems(players_dict):
    handles = players_dict.keys()
    banned = set()
    print(f"Fetching history for: {list(handles)}")
    
    for handle in handles:
        try:
            # We assume < 100k submissions, safe limit
            url = f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=10000"
            resp = requests.get(url, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if data['status'] == 'OK':
                    for sub in data['result']:
                        if sub.get('verdict') == 'OK':
                            p = sub['problem']
                            c_id = p.get('contestId')
                            idx = p.get('index')
                            if c_id and idx:
                                pid = str(c_id) + str(idx)
                                banned.add(pid)
        except Exception as e:
            print(f"Error fetching {handle}: {e}")

    return list(banned)

# --- WEBSOCKET EVENTS ---

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
                'rating': user.rating
            }
            # Initialize score entry if missing
            if handle not in lobbies[room]['scores']:
                lobbies[room]['scores'][handle] = {'solved': [], 'penalty': 0}
        
        emit('update_players', list(lobbies[room]['players'].values()), room=room)
        emit('update_leaderboard', lobbies[room]['scores'], room=room)

@socketio.on('toggle_ready')
def on_ready(data):
    room = data['room']
    handle = data['handle']
    if room in lobbies and handle in lobbies[room]['players']:
        curr = lobbies[room]['players'][handle]['ready']
        lobbies[room]['players'][handle]['ready'] = not curr
        emit('update_players', list(lobbies[room]['players'].values()), room=room)

@socketio.on('start_game')
def on_start(data):
    room = data['room']
    lobby = lobbies.get(room)
    if not lobby: return

    # 1. CHECK READY
    players = lobby['players']
    not_ready = [p['handle'] for p in players.values() if not p['ready']]
    if not_ready:
        emit('error', f"Waiting for: {', '.join(not_ready)}", room=room)
        return

    # 2. START LOGIC
    try:
        banned_ids = get_banned_problems(players)
        
        payload = {
            "mode": lobby['mode'],
            "rating": lobby['rating'],
            "banned_ids": banned_ids,
            "players": list(players.keys())
        }

        resp = requests.post(CPP_ENGINE_URL, json=payload)
        game_data = resp.json()

        if "problems" in game_data:
            lobby['state'] = 'running'
            lobby['start_time'] = time.time()
            lobby['problem_status'] = {p['id']: None for p in game_data['problems']}
            
            game_data['track_ids'] = [p['id'] for p in game_data['problems']]
            game_data['end_time'] = lobby['start_time'] + (lobby['duration'] * 60)
            
            emit('game_started', game_data, room=room)
        else:
            emit('error', game_data.get("error", "No problem found"), room=room)

    except Exception as e:
        print(f"Start Error: {e}")
        emit('error', f"Backend Error: {str(e)}", room=room)

@socketio.on('report_solve')
def on_report_solve(data):
    room = data['room']
    handle = data['winner']
    pid = data['problem_id']
    
    lobby = lobbies.get(room)
    if not lobby or lobby['state'] != 'running': return

    # 1. LOCKOUT CHECK
    if lobby['mode'] == 'lockout':
        if lobby['problem_status'].get(pid) is not None:
            return # Already claimed

    # 2. VERIFY WITH API
    try:
        url = f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=5"
        resp = requests.get(url, timeout=5).json()
        verified = False
        
        if resp['status'] == 'OK':
            for sub in resp['result']:
                if sub.get('verdict') == 'OK':
                    p = sub['problem']
                    c_id = p.get('contestId')
                    idx = p.get('index')
                    if c_id and idx:
                        if (str(c_id) + str(idx)) == pid:
                            verified = True
                            break
        
        if not verified: return

        # 3. UPDATE STATE
        if lobby['mode'] == 'lockout':
            lobby['problem_status'][pid] = handle
            emit('problem_locked', {'id': pid, 'winner': handle}, room=room)
        
        score_data = lobby['scores'][handle]
        if pid not in score_data['solved']:
            score_data['solved'].append(pid)
            score_data['penalty'] += int((time.time() - lobby['start_time']) / 60)
            
            emit('update_leaderboard', lobby['scores'], room=room)
            emit('notification', f"{handle} solved {pid}!", room=room)

        # 4. GAME END (1v1 Classic Only)
        if lobby['mode'] == 'classic':
            handle_game_end(room, winner=handle)

    except Exception as e:
        print(f"Verification Error: {e}")

def handle_game_end(room, winner):
    lobby = lobbies[room]
    lobby['state'] = 'ended'
    
    players = list(lobby['players'].keys())
    # Identify loser (assuming 1v1 for classic)
    loser = None
    for p in players:
        if p != winner:
            loser = p
            break
            
    # Calculate ELO if we have 2 players
    if loser:
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
    else:
        # Fallback if solo testing
        emit('game_over', {'winner': winner, 'problem': 'Classic Duel', 'new_ratings': {}, 'change': 0}, room=room)

if __name__ == '__main__':
    socketio.run(app, port=8080, debug=True)