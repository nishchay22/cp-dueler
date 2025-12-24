import eventlet
# 1. CRITICAL: Patch must be the very first line
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
import requests
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev_key'
# Use eventlet for high-speed async support
socketio = SocketIO(app, async_mode='eventlet')

# Configuration
CPP_ENGINE_URL = "http://127.0.0.1:5001/get-duel"
lobbies = {}
game_states = {} # Tracks if a game is 'running' or 'ended' to prevent double-wins

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create', methods=['POST'])
def create():
    room_id = str(uuid.uuid4())[:8] # 8-char ID for privacy
    lobbies[room_id] = {
        'mode': request.form.get('mode'),
        'rating': int(request.form.get('rating') or 0),
        'players': {}, # Dict: {'handle': {'handle': 'Name', 'ready': False}}
        'state': 'waiting'
    }
    return jsonify({'room_id': room_id})

@app.route('/lobby/<room_id>')
def lobby(room_id):
    if room_id not in lobbies: return "Lobby not found", 404
    return render_template('lobby.html', room_id=room_id, mode=lobbies[room_id]['mode'])

# --- HELPER: ROBUST CF API FETCH ---
def get_banned_problems(players_dict):
    handles = players_dict.keys()
    banned = set()
    print(f"Fetching history for: {list(handles)}")
    
    for handle in handles:
        try:
            # 10s timeout to handle Codeforces lag
            url = f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=5000"
            resp = requests.get(url, timeout=10) 
            
            if resp.status_code != 200:
                print(f"❌ Error: {handle} returned HTTP {resp.status_code}")
                raise Exception(f"CF API Error {resp.status_code} for {handle}")

            data = resp.json()
            
            if data['status'] != 'OK':
                comment = data.get('comment', 'Unknown error')
                print(f"❌ API Failed for {handle}: {comment}")
                raise Exception(f"{handle}: {comment}")

            # Parse solved problems
            count = 0
            for sub in data['result']:
                if sub.get('verdict') == 'OK':
                    p = sub['problem']
                    # ID format matches C++ backend: "1400A"
                    pid = str(p.get('contestId', '')) + p.get('index', '')
                    banned.add(pid)
                    count += 1
                    
            print(f"✅ Loaded {count} solved for {handle}")

        except requests.exceptions.Timeout:
            print(f"❌ Timeout fetching {handle}")
            raise Exception(f"Connection timed out for {handle}. CF is slow.")
            
        except Exception as e:
            print(f"❌ Crash on {handle}: {str(e)}")
            raise e 

    return list(banned)

# --- WEBSOCKET EVENTS ---

@socketio.on('join')
def on_join(data):
    room = data['room']
    handle = data['handle']
    join_room(room)
    
    if room in lobbies:
        # Initialize player if new
        if handle not in lobbies[room]['players']:
            lobbies[room]['players'][handle] = {'handle': handle, 'ready': False}
        
        # Broadcast list
        emit('update_players', list(lobbies[room]['players'].values()), room=room)

@socketio.on('toggle_ready')
def on_ready(data):
    room = data['room']
    handle = data['handle']
    
    if room in lobbies and handle in lobbies[room]['players']:
        # Flip Ready Status
        curr = lobbies[room]['players'][handle]['ready']
        lobbies[room]['players'][handle]['ready'] = not curr
        
        emit('update_players', list(lobbies[room]['players'].values()), room=room)

@socketio.on('start_game')
def on_start(data):
    room = data['room']
    lobby = lobbies.get(room)
    if not lobby: return

    players = lobby['players']
    
    # 1. CHECK: Are there players?
    if len(players) < 1:
        emit('error', "Need at least 1 player!", room=room)
        return

    # 2. CHECK: Is everyone ready?
    not_ready = [p['handle'] for p in players.values() if not p['ready']]
    if not_ready:
        emit('error', f"Waiting for: {', '.join(not_ready)}", room=room)
        return

    # 3. START: Fetch Banned List & Call C++
    try:
        banned_ids = get_banned_problems(players)
        
        payload = {
            "mode": lobby['mode'],
            "rating": lobby['rating'],
            "banned_ids": banned_ids,
            "players": list(players.keys())
        }

        print(f"Requesting C++ Engine for room {room}...")
        resp = requests.post(CPP_ENGINE_URL, json=payload)
        game_data = resp.json()

        if "problems" in game_data:
            lobby['state'] = 'running'
            game_states[room] = 'running' # Mark as active
            
            # Extract IDs for frontend tracking (e.g. ["1400A"])
            track_ids = [p['id'] for p in game_data['problems']] if 'problems' in game_data else []
            game_data['track_ids'] = track_ids
            
            emit('game_started', game_data, room=room)
        else:
            emit('error', game_data.get("error", "No problem found"), room=room)

    except Exception as e:
        print(f"Start Error: {e}")
        emit('error', f"Error: {str(e)}", room=room)

# --- VICTORY VERIFICATION SYSTEM ---
@socketio.on('claim_victory')
def on_claim(data):
    room = data['room']
    winner = data['winner']
    problem_id = data['problem_id'] # e.g. "1400A"
    
    # Prevent duplicate wins
    if game_states.get(room) != 'running': return
    
    print(f"Verifying claim: {winner} solved {problem_id}...")

    # Official Server-Side Check
    try:
        # Check last 5 subs to be safe
        url = f"https://codeforces.com/api/user.status?handle={winner}&from=1&count=5"
        resp = requests.get(url, timeout=5).json()
        
        if resp['status'] == 'OK':
            for sub in resp['result']:
                if sub.get('verdict') == 'OK':
                    p = sub['problem']
                    pid = str(p.get('contestId', '')) + p.get('index', '')
                    
                    if pid == problem_id:
                        print(f"🏆 VICTORY CONFIRMED: {winner}")
                        
                        # 1. Update State
                        game_states[room] = 'ended'
                        lobby = lobbies.get(room)
                        if lobby: lobby['state'] = 'ended'
                        
                        # 2. Notify All Clients
                        emit('game_over', {'winner': winner, 'problem': pid}, room=room)
                        return
                        
    except Exception as e:
        print(f"Verification failed: {e}")

if __name__ == '__main__':
    socketio.run(app, port=8080, debug=True)