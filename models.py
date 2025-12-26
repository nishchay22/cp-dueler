from flask_sqlalchemy import SQLAlchemy
import math

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    handle = db.Column(db.String(80), unique=True, nullable=False)
    rating = db.Column(db.Integer, default=1500) # Internal Rating
    matches_played = db.Column(db.Integer, default=0)

class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    winner_handle = db.Column(db.String(80))
    loser_handle = db.Column(db.String(80))
    mode = db.Column(db.String(20))
    rating_change = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

# --- ELO LOGIC ---
def calculate_elo_change(winner_rating, loser_rating):
    # K-factor determines how volatile ratings are. 32 is standard for new players.
    K = 32
    
    # Probability of winning
    prob_winner = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
    
    # Result is 1 for win
    new_rating = winner_rating + K * (1 - prob_winner)
    
    change = int(new_rating - winner_rating)
    return change