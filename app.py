import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
import requests
import uuid
import time # Needed for timer/penalty

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev_key'
socketio = SocketIO(app, async_mode='eventlet')

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
        'rating': int(request.form.get('rating') or 0),
        'duration': int(request.form.get('duration') or 30), # Default 30 mins
        'players': {}, 
        'state': 'waiting',
        'start_time': 0,
        'scores': {} # Stores contest scores: {handle: {'solved': [], 'penalty': 0}}
    }
    return jsonify({'room_id': room_id})

@app.route('/lobby/<room_id>')
def lobby(room_id):
    if room_id not in lobbies: return "Lobby not found", 404
    lobby_data = lobbies[room_id]
    return render_template('lobby.html', room_id=room_id, mode=lobby_data['mode'], duration=lobby_data['duration'])

# --- HELPER: Fetch Banned Problems (Same as before) ---
def get_banned_problems(players_dict):
    handles = players_dict.keys()
    banned = set()
    for handle in handles:
        try:
            url = f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=2000"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data['status'] == 'OK':
                    for sub in data['result']:
                        if sub.get('verdict') == 'OK':
                            p = sub['problem']
                            pid = str(p.get('contestId', '')) + p.get('index', '')
                            banned.add(pid)
        except:
            pass # Fail silently for speed
    return list(banned)

# --- WEBSOCKETS ---

@socketio.on('join')
def on_join(data):
    room = data['room']
    handle = data['handle']
    join_room(room)
    if room in lobbies:
        if handle not in lobbies[room]['players']:
            lobbies[room]['players'][handle] = {'handle': handle, 'ready': False}
            # Initialize Score
            lobbies[room]['scores'][handle] = {'solved': [], 'penalty': 0}
        
        emit('update_players', list(lobbies[room]['players'].values()), room=room)
        # Send current leaderboard immediately
        emit('update_leaderboard', lobbies[room]['scores'], room=room)

@socketio.on('toggle_ready')
def on_ready(data):
    room = data['room']
    handle = data['handle']
    if room in lobbies:
        curr = lobbies[room]['players'][handle]['ready']
        lobbies[room]['players'][handle]['ready'] = not curr
        emit('update_players', list(lobbies[room]['players'].values()), room=room)

@socketio.on('start_game')
def on_start(data):
    room = data['room']
    lobby = lobbies.get(room)
    if not lobby: return

    # Check Ready
    players = lobby['players']
    not_ready = [p['handle'] for p in players.values() if not p['ready']]
    if not_ready:
        emit('error', f"Waiting for: {', '.join(not_ready)}", room=room)
        return

    # Fetch Problems
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
            lobby['start_time'] = time.time() # Start the Clock!
            
            track_ids = [p['id'] for p in game_data['problems']]
            game_data['track_ids'] = track_ids
            # Send End Time so clients can sync countdown
            game_data['end_time'] = lobby['start_time'] + (lobby['duration'] * 60)
            
            emit('game_started', game_data, room=room)
        else:
            emit('error', "Could not generate problems.", room=room)
    except Exception as e:
        emit('error', str(e), room=room)

# --- NEW: UNIFIED SOLVE HANDLER ---
@socketio.on('report_solve')
def on_report_solve(data):
    room = data['room']
    handle = data['winner']
    problem_id = data['problem_id']
    
    lobby = lobbies.get(room)
    if not lobby or lobby['state'] != 'running': return

    # 1. Verify with CF API
    try:
        url = f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=5"
        resp = requests.get(url, timeout=5).json()
        verified = False
        
        if resp['status'] == 'OK':
            for sub in resp['result']:
                if sub.get('verdict') == 'OK':
                    p = sub['problem']
                    pid = str(p.get('contestId', '')) + p.get('index', '')
                    if pid == problem_id:
                        verified = True
                        break
        
        if not verified: return

        # 2. Handle Logic Based on Mode
        
        # A. CLASSIC MODE (1v1) -> First solve wins immediately
        if lobby['mode'] == 'classic':
            emit('game_over', {'winner': handle, 'problem': problem_id}, room=room)
            lobby['state'] = 'ended'
            
        # B. CONTEST MODE -> Update Leaderboard
        else:
            score_data = lobby['scores'][handle]
            
            # Only count if not already solved
            if problem_id not in score_data['solved']:
                score_data['solved'].append(problem_id)
                
                # Calculate Penalty: Time in minutes since start
                elapsed_mins = int((time.time() - lobby['start_time']) / 60)
                score_data['penalty'] += elapsed_mins
                
                # Broadcast Update
                emit('update_leaderboard', lobby['scores'], room=room)
                emit('notification', f"{handle} solved {problem_id}!", room=room)

    except Exception as e:
        print(f"Verification Error: {e}")

if __name__ == '__main__':
    socketio.run(app, port=8080, debug=True)