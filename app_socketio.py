import logging
import os
import json
from flask import Flask, request, jsonify
from flask_socketio import SocketIO
from dotenv import load_dotenv
from deepgram import (
    DeepgramClient,
    LiveTranscriptionEvents,
    LiveOptions,
    DeepgramClientOptions
)
from medical_chatbot import MedicalChatbot

chatbot = MedicalChatbot()

load_dotenv()

app_socketio = Flask("app_socketio")
socketio = SocketIO(app_socketio, cors_allowed_origins=['http://127.0.0.1:5002'])

API_KEY = os.getenv("DEEPGRAM_API_KEY")

transcriptions = []  # List to store transcriptions

# Set up client configuration
config = DeepgramClientOptions(
    verbose=logging.WARN,  # Change to logging.INFO or logging.DEBUG for more verbose output
    options={"keepalive": "true"}
)

deepgram = DeepgramClient(API_KEY, config)

dg_connection = None

def initialize_deepgram_connection():
    global dg_connection
    dg_connection = deepgram.listen.live.v("1")

    def on_open(self, open, **kwargs):
        print(f"\n\n{open}\n\n")

    def on_message(self, result, **kwargs):
        transcript = result.channel.alternatives[0].transcript
        if len(transcript) > 0:
            print(result.channel.alternatives[0].transcript)
            socketio.emit('transcription_update', {'transcription': transcript})
            transcriptions.append(transcript)  # Append transcript to the list

    def on_close(self, close, **kwargs):
        print(f"\n\n{close}\n\n")
        save_transcriptions_to_json()  # Save when session closes

    def on_error(self, error, **kwargs):
        print(f"\n\n{error}\n\n")

    dg_connection.on(LiveTranscriptionEvents.Open, on_open)
    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
    dg_connection.on(LiveTranscriptionEvents.Close, on_close)
    dg_connection.on(LiveTranscriptionEvents.Error, on_error)

    options = LiveOptions(
            model="nova-2",
            language="en-US",
            sample_rate=16000
        )

    if not dg_connection.start(options):
        print("Failed to start connection")
        exit()

def save_transcriptions_to_json():
    full_transcript = " ".join(transcriptions)
    with open('recorded.json', 'w') as f:
        json.dump({"transcript": full_transcript}, f)
    print("Transcriptions saved to recorded.json")
    transcriptions.clear()

@socketio.on('audio_stream')
def handle_audio_stream(data):
    if dg_connection:
        dg_connection.send(data)

@socketio.on('toggle_transcription//<patient_id>')
def handle_toggle_transcription(data, patient_id):
    print("toggle_transcription", data)
    action = data.get("action")
    if action == "start":
        print("Starting Deepgram connection")
        initialize_deepgram_connection()
    if action != "start":
        save_transcriptions_to_json()
        with open('recorded.json', 'r') as f:
            data = json.load(f)
        full_transcript = data["transcript"]
        response = chatbot.generate_response(full_transcript)
        finished = chatbot.should_stop(response)
        if finished:
            report_content = chatbot.create_report().choices[0].message.content
            report_data = chatbot.extract_and_save_report(report_content, patient_id)

            if isinstance(report_data, dict):
                report_data['_id'] = str(report_data.get('_id'))

            socketio.emit(report_data)
        socketio.emit('transcription_response', {'response': response, 'finished': finished})

@socketio.on('response/<patient_id>')
def report(patient_id):
    if chatbot.finished:
        report_content = chatbot.create_report().choices[0].message.content
        report_data = chatbot.extract_and_save_report(report_content, patient_id)

        if isinstance(report_data, dict):
            report_data['_id'] = str(report_data.get('_id'))

        socketio.emit(report_data)
    else:
        socketio.emit({"error": "Chat not finished"}), 400

@socketio.on('connect')
def server_connect():
    print('Client connected')

@socketio.on('restart_deepgram')
def restart_deepgram():
    print('Restarting Deepgram connection')
    initialize_deepgram_connection()

if __name__ == '__main__':
    logging.info("Starting SocketIO server.")
    socketio.run(app_socketio, debug=True, allow_unsafe_werkzeug=True, port=5001)
