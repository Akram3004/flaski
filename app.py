from flask import Flask, render_template, request
import base64
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import requests
import os
import json
from google.oauth2 import service_account

app = Flask(__name__)

# Google API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate_gmail():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

def authenticate_gemini():
    # Check if we're using a service account or API key
    if os.path.exists('gemini_service_account.json'):
        credentials = service_account.Credentials.from_service_account_file(
            'gemini_service_account.json',
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        return credentials
    elif os.getenv('GEMINI_API_KEY'):
        return os.getenv('GEMINI_API_KEY')
    else:
        raise ValueError("No Gemini API credentials found. Please set up either a service account or API key.")

def fetch_resumes(service):
    results = service.users().messages().list(userId='me', q='filename:pdf OR filename:doc OR filename:docx').execute()
    messages = results.get('messages', [])
    
    resumes = []

    for message in messages:
        msg = service.users().messages().get(userId='me', id=message['id']).execute()
        payload = msg.get('payload', {})
        parts = payload.get('parts', [])
        
        for part in parts:
            filename = part.get('filename')
            body = part.get('body', {})
            attachment_id = body.get('attachmentId')
            
            if filename and (filename.endswith('.pdf') or filename.endswith('.doc') or filename.endswith('.docx')):
                if attachment_id:
                    attachment = service.users().messages().attachments().get(
                        userId='me',
                        messageId=message['id'],
                        id=attachment_id
                    ).execute()
                    
                    data = attachment.get('data')
                    if data:
                        decoded_data = base64.urlsafe_b64decode(data)
                        resume_url = f"https://mail.google.com/mail/u/0/#inbox/{message['id']}"
                        
                        resumes.append({
                            'filename': filename,
                            'url': resume_url,
                            'content': decoded_data.decode('utf-8', errors='ignore')
                        })
    
    print(f"Total resumes fetched: {len(resumes)}")
    return resumes

def analyze_resume_with_gemini(resume_content, job_description, auth_credentials):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
    
    if isinstance(auth_credentials, service_account.Credentials):
        auth_credentials.refresh(Request())
        headers = {
            "Authorization": f"Bearer {auth_credentials.token}",
            "Content-Type": "application/json"
        }
    else:  # API Key
        headers = {
            "Authorization": f"Bearer {auth_credentials}",
            "Content-Type": "application/json"
        }
    
    prompt = f"""
    Analyze the following resume against the given job description. 
    Provide a match score out of 100 and a brief explanation of the score.

    Resume:
    {resume_content}

    Job Description:
    {job_description}

    Response format:
    {{
        "match_score": <score>,
        "explanation": "<brief explanation>"
    }}
    """

    payload = {
        "contents": [{"parts":[{"text": prompt}]}]
    }
    
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 200:
        content = response.json()['candidates'][0]['content']['parts'][0]['text']
        return json.loads(content)
    else:
        print("Error:", response.status_code, response.text)
        return None

@app.route('/', methods=['GET', 'POST'])
def dashboard():
    if request.method == 'POST':
        job_description = request.form['job_description']
        
        try:
            # Authenticate Gmail API
            gmail_creds = authenticate_gmail()
            service = build('gmail', 'v1', credentials=gmail_creds)

            # Fetch resumes
            resumes = fetch_resumes(service)

            # Authenticate Gemini API
            gemini_auth = authenticate_gemini()

            # Analyze resumes using Gemini API
            analyzed_resumes = []
            for resume in resumes:
                analysis_result = analyze_resume_with_gemini(resume['content'], job_description, gemini_auth)
                if analysis_result:
                    analyzed_resumes.append({
                        'filename': resume['filename'],
                        'url': resume['url'],
                        'match_score': analysis_result['match_score'],
                        'explanation': analysis_result['explanation']
                    })

            return render_template('dashboard.html', resumes=analyzed_resumes, job_description=job_description)
        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            return render_template('dashboard.html', error=error_message)
    
    return render_template('dashboard.html')

if __name__ == '__main__':
    app.run(debug=True)