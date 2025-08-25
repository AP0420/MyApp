import streamlit as st
import pandas as pd
import random
import time
from datetime import datetime
import hashlib
import sqlite3
import os
from streamlit.components.v1 import html

# Use a persistent database path for deployment
def get_db_path():
    if os.path.exists('chat_app.db'):
        return 'chat_app.db'
    else:
        return '/tmp/chat_app.db'  # Use tmp directory for deployment

# Initialize session state
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'current_user' not in st.session_state:
    st.session_state.current_user = None
if 'chat_partner' not in st.session_state:
    st.session_state.chat_partner = None
if 'chat_messages' not in st.session_state:
    st.session_state.chat_messages = []
if 'waiting_for_match' not in st.session_state:
    st.session_state.waiting_for_match = False
if 'in_chat' not in st.session_state:
    st.session_state.in_chat = False
if 'db_initialized' not in st.session_state:
    st.session_state.db_initialized = False
if 'available_users' not in st.session_state:
    st.session_state.available_users = []
if 'session_id' not in st.session_state:
    st.session_state.session_id = None

# Database setup
def init_db():
    if st.session_state.db_initialized:
        return
        
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Users table with online status
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE,
                  password TEXT,
                  gender TEXT,
                  preference TEXT,
                  interests TEXT,
                  online BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Chat sessions table
    c.execute('''CREATE TABLE IF NOT EXISTS chat_sessions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user1_id INTEGER,
                  user2_id INTEGER,
                  start_time TIMESTAMP,
                  end_time TIMESTAMP)''')
    
    # Messages table
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id INTEGER,
                  sender_id INTEGER,
                  message TEXT,
                  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Add online status column if it doesn't exist
    try:
        c.execute("ALTER TABLE users ADD COLUMN online BOOLEAN DEFAULT FALSE")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    conn.commit()
    conn.close()
    st.session_state.db_initialized = True

# Password hashing
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# User registration
def register_user(username, password, gender, preference, interests):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    hashed_password = hash_password(password)
    interests_str = ','.join(interests)
    
    try:
        c.execute("INSERT INTO users (username, password, gender, preference, interests, online) VALUES (?, ?, ?, ?, ?, ?)",
                  (username, hashed_password, gender, preference, interests_str, False))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

# User authentication
def authenticate_user(username, password):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    hashed_password = hash_password(password)
    
    c.execute("SELECT id, username, gender, preference, interests FROM users WHERE username = ? AND password = ?",
              (username, hashed_password))
    user = c.fetchone()
    
    # Mark user as online
    if user:
        c.execute("UPDATE users SET online = TRUE WHERE username = ?", (username,))
        conn.commit()
    
    conn.close()
    
    if user:
        return {
            'id': user[0],
            'username': user[1],
            'gender': user[2],
            'preference': user[3],
            'interests': user[4].split(',') if user[4] else []
        }
    return None

# Mark user as offline
def set_user_offline(user_id):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute("UPDATE users SET online = FALSE WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

# Find a matching partner from REAL users in the database
def find_match(current_user):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Get user's preference and gender
    user_pref = current_user['preference']
    user_gender = current_user['gender']
    user_interests = current_user['interests']
    
    # Define target gender based on preference
    if user_pref == 'Straight':
        if user_gender == 'Male':
            target_gender = 'Female'
        else:
            target_gender = 'Male'
    elif user_pref == 'Gay':
        target_gender = 'Male'
    elif user_pref == 'Lesbian':
        target_gender = 'Female'
    else:  # Bisexual
        target_gender = None  # No gender restriction
    
    # Build query based on preference
    if target_gender:
        query = """
            SELECT id, username, gender, preference, interests 
            FROM users 
            WHERE id != ? 
            AND gender = ?
            AND online = TRUE
            AND (preference = ? OR preference = 'Bisexual')
        """
        params = (current_user['id'], target_gender, user_pref)
    else:
        # For bisexual users, match with anyone who would also match with them
        query = """
            SELECT id, username, gender, preference, interests 
            FROM users 
            WHERE id != ? 
            AND online = TRUE
            AND (
                (gender = 'Male' AND (preference = 'Bisexual' OR preference = 'Gay' OR preference = 'Straight' AND gender = 'Female')) OR
                (gender = 'Female' AND (preference = 'Bisexual' OR preference = 'Lesbian' OR preference = 'Straight' AND gender = 'Male'))
            )
        """
        params = (current_user['id'],)
    
    c.execute(query, params)
    potential_matches = c.fetchall()
    
    # Convert to list of dictionaries and calculate common interests
    matches = []
    for match in potential_matches:
        match_interests = match[4].split(',') if match[4] else []
        common_interests = set(user_interests).intersection(set(match_interests))
        
        matches.append({
            'id': match[0],
            'username': match[1],
            'gender': match[2],
            'preference': match[3],
            'common_interests': list(common_interests)
        })
    
    # Sort by number of common interests (most first)
    matches.sort(key=lambda x: len(x['common_interests']), reverse=True)
    
    conn.close()
    
    # Return the best match if available
    return matches[0] if matches else None

# Start a chat session
def start_chat_session(user1_id, user2_id):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute("INSERT INTO chat_sessions (user1_id, user2_id, start_time) VALUES (?, ?, ?)",
              (user1_id, user2_id, datetime.now()))
    session_id = c.lastrowid
    
    conn.commit()
    conn.close()
    
    return session_id

# Send a message
def send_message(session_id, sender_id, message):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute("INSERT INTO messages (session_id, sender_id, message) VALUES (?, ?, ?)",
              (session_id, sender_id, message))
    
    conn.commit()
    conn.close()

# Get chat messages
def get_messages(session_id):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute("""
        SELECT sender_id, message, timestamp 
        FROM messages 
        WHERE session_id = ? 
        ORDER BY timestamp ASC
    """, (session_id,))
    
    messages = c.fetchall()
    conn.close()
    
    return messages

# Initialize database
init_db()

# App layout
st.set_page_config(page_title="Anonymous Chat", page_icon="💬", layout="wide")

# Custom CSS
st.markdown("""
    <style>
    .main {
        background-color: #f8f9fa;
    }
    .stButton>button {
        width: 100%;
        background-color: #4CAF50;
        color: white;
    }
    .chat-container {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 20px;
        height: 500px;
        overflow-y: scroll;
    }
    .message {
        padding: 10px;
        border-radius: 10px;
        margin-bottom: 10px;
    }
    .user-message {
        background-color: #dcf8c6;
        margin-left: 20%;
    }
    .partner-message {
        background-color: #ffffff;
        margin-right: 20%;
    }
    .waiting {
        text-align: center;
        padding: 40px;
    }
    </style>
    """, unsafe_allow_html=True)

# Main app logic
if not st.session_state.logged_in:
    # Login/Registration page
    tab1, tab2 = st.tabs(["Login", "Register"])
    
    with tab1:
        st.header("Login to Anonymous Chat")
        login_username = st.text_input("Username", key="login_username")
        login_password = st.text_input("Password", type="password", key="login_password")
        
        if st.button("Login"):
            user = authenticate_user(login_username, login_password)
            if user:
                st.session_state.logged_in = True
                st.session_state.current_user = user
                st.success("Logged in successfully!")
                st.rerun()
            else:
                st.error("Invalid username or password")
    
    with tab2:
        st.header("Create a New Account")
        reg_username = st.text_input("Choose a Username", key="reg_username")
        reg_password = st.text_input("Choose a Password", type="password", key="reg_password")
        reg_gender = st.selectbox("Gender", ["Male", "Female", "Other"], key="reg_gender")
        reg_preference = st.selectbox("Preference", ["Straight", "Gay", "Lesbian", "Bisexual"], key="reg_preference")
        reg_interests = st.multiselect("Interests", 
                                      ["Music", "Sports", "Movies", "Books", "Travel", "Food", "Art", "Technology", "Gaming", "Fitness"],
                                      key="reg_interests")
        
        if st.button("Register"):
            if reg_username and reg_password and reg_interests:
                if register_user(reg_username, reg_password, reg_gender, reg_preference, reg_interests):
                    st.success("Account created successfully! Please login.")
                else:
                    st.error("Username already exists. Please choose another.")
            else:
                st.error("Please fill all fields")

else:
    # User is logged in
    st.sidebar.title(f"Welcome, {st.session_state.current_user['username']}!")
    st.sidebar.write(f"Gender: {st.session_state.current_user['gender']}")
    st.sidebar.write(f"Preference: {st.session_state.current_user['preference']}")
    st.sidebar.write(f"Interests: {', '.join(st.session_state.current_user['interests'])}")
    
    if st.sidebar.button("Logout"):
        # Mark user as offline
        set_user_offline(st.session_state.current_user['id'])
        
        st.session_state.logged_in = False
        st.session_state.current_user = None
        st.session_state.chat_partner = None
        st.session_state.chat_messages = []
        st.session_state.waiting_for_match = False
        st.session_state.in_chat = False
        st.session_state.session_id = None
        st.rerun()
    
    if not st.session_state.in_chat:
        # Main page - not in chat
        st.title("Anonymous Chat Platform")
        st.write("Click the button below to find someone to chat with based on your preferences and interests.")
        
        if st.button("Find a Chat Partner"):
            st.session_state.waiting_for_match = True
            st.rerun()
        
        if st.session_state.waiting_for_match:
            st.info("Looking for a matching partner...")
            
            # Find a real match from the database
            with st.spinner("Searching for someone with similar interests..."):
                # Try to find a match multiple times with delay
                max_attempts = 10
                for attempt in range(max_attempts):
                    partner = find_match(st.session_state.current_user)
                    if partner:
                        break
                    time.sleep(1)  # Wait 1 second between attempts
                
                if partner:
                    st.session_state.chat_partner = partner
                    st.session_state.waiting_for_match = False
                    st.session_state.in_chat = True
                    st.session_state.chat_messages = []
                    
                    # Start a chat session
                    st.session_state.session_id = start_chat_session(
                        st.session_state.current_user['id'],
                        partner['id']
                    )
                    
                    st.rerun()
                else:
                    st.error("No suitable matches found. Please try again later.")
                    st.session_state.waiting_for_match = False
                    st.rerun()
    
    else:
        # Chat interface
        st.title("Anonymous Chat")
        
        if st.session_state.chat_partner:
            partner = st.session_state.chat_partner
            st.subheader(f"Chat with: Anonymous ({partner['gender']}, {partner['preference']})")
            st.write(f"Common interests: {', '.join(partner['common_interests'])}")
        
        # Chat container
        chat_container = st.container(height=400)
        
        with chat_container:
            for msg in st.session_state.chat_messages:
                if msg['sender'] == 'You':
                    st.markdown(f"<div class='message user-message'><b>You:</b> {msg['text']}</div>", 
                               unsafe_allow_html=True)
                else:
                    st.markdown(f"<div class='message partner-message'><b>Partner:</b> {msg['text']}</div>", 
                               unsafe_allow_html=True)
        
        # Message input
        col1, col2 = st.columns([6, 1])
        with col1:
            new_message = st.text_input("Type your message", key="new_message", label_visibility="collapsed")
        with col2:
            send_btn = st.button("Send")
        
        if send_btn and new_message:
            # Add user message
            st.session_state.chat_messages.append({
                'sender': 'You',
                'text': new_message
            })
            
            # Save message to database
            send_message(
                st.session_state.session_id,
                st.session_state.current_user['id'],
                new_message
            )
            
            # Simulate partner response after a short delay
            responses = [
                "That's interesting! Tell me more.",
                "I feel the same way about that.",
                "I've never thought about it like that before.",
                "What made you think of that?",
                f"I also enjoy {random.choice(partner['common_interests'])}!",
                "That's cool!",
                "I understand what you mean.",
                "Can you elaborate on that?",
                "That's a unique perspective."
            ]
            
            # Add to session state and rerun to show user message immediately
            st.rerun()
            
            # Add simulated response after a brief delay
            time.sleep(1)
            st.session_state.chat_messages.append({
                'sender': 'Partner',
                'text': random.choice(responses)
            })
            
            # Save partner message to database
            send_message(
                st.session_state.session_id,
                partner['id'],
                random.choice(responses)
            )
            
            st.rerun()
        
        if st.button("End Chat"):
            # Mark user as offline
            set_user_offline(st.session_state.current_user['id'])
            
            st.session_state.chat_partner = None
            st.session_state.chat_messages = []
            st.session_state.in_chat = False
            st.session_state.session_id = None
            st.rerun()
