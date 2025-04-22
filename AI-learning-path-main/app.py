from flask import Flask, render_template, request, session, redirect, url_for
from flask import render_template_string
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import google.generativeai as genai
import markdown
from markdown.extensions.fenced_code import FencedCodeExtension
import re
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json
import requests
from flask_weasyprint import HTML, render_pdf
from weasyprint import CSS

print("GOOGLE_API_KEY exists:", os.getenv("GOOGLE_API_KEY") is not None)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'harshkey'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)

# Configure Generative AI
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
model = genai.GenerativeModel('models/gemini-1.5-flash')

# Create a Markdown instance with the FencedCodeExtension
md = markdown.Markdown(extensions=[FencedCodeExtension()])

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_name = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True)
    email = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(80))

    # ✅ Cascade delete added here:
    courses = db.relationship(
        'Course',
        backref='user',
        lazy=True,
        cascade="all, delete-orphan"
    )

    date_joined = db.Column(db.DateTime, default=datetime.now)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route("/quiz_interface")
def quiz_interface():
    return render_template("home.html")

@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    if request.method == "POST":
        print(request.form)
        language = request.form["language"]
        questions = request.form["ques"]
        choices = request.form["choices"]

        # Generate quiz using Gemini
        prompt = f"""Generate a quiz in JSON format with the following requirements:
        - Topic: {language}
        - Number of questions: {questions}
        - Choices per question: {choices}
        - Format: {{
            "topic": "topic name",
            "questions": [
                {{
                    "question": "question text",
                    "choices": ["choice1", "choice2", ...],
                    "answer": "correct answer"
                }}
            ]
        }}
        Ensure valid JSON format without markdown formatting."""
        
        response = model.generate_content(prompt)
        quiz_content = response.text
        
        # Clean response and parse JSON
        quiz_content = quiz_content.replace('```json', '').replace('```', '').strip()
        quiz_content = json.loads(quiz_content)
        
        session['response'] = quiz_content
        return render_template("quiz.html", quiz_content=quiz_content)
    
    if request.method == "GET":
        score = 0
        actual_answers = []
        given_answers = list(request.args.values()) or []
        res = session.get('response', None)
        for answer in res["questions"]:
            actual_answers.append(answer["answer"])
        if len(given_answers) != 0:
            for i in range(len(actual_answers)):
                if actual_answers[i] == given_answers[i]:
                    score += 1
        return render_template("score.html", actual_answers=actual_answers, 
                             given_answers=given_answers, score=score)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        hashed_password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        new_user = User(username=request.form['username'], email=request.form['email'], password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')




@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
    return render_template('login.html')




@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))




@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_authenticated:
        return render_template('dashboard.html', user=current_user)
    else:
        return redirect(url_for('login'))





@app.route('/')
def home():
    if current_user.is_authenticated:
        saved_courses = Course.query.filter_by(user_id=current_user.id).all()
        recommended_courses = generate_recommendations(saved_courses)
        return render_template('app.html', saved_courses=saved_courses, recommended_courses = recommended_courses, user=current_user)
    else:
        return redirect(url_for('login'))




@app.route('/course', methods=['GET', 'POST'])
@login_required
def course():
    if request.method == 'POST':
        course_name = request.form['course_name']
        completions = generate_text(course_name)
        print(f"course_name: {course_name}")
        rendered = render_template('courses/course1.html', completions=completions, course_name=course_name)
        new_course = Course(course_name=course_name, content=rendered, user_id=current_user.id)
        db.session.add(new_course)
        db.session.commit()
        return rendered
    return render_template('courses/course1.html')




@app.route('/r_course/<course_name>', methods=['GET', 'POST'])
@login_required
def r_course(course_name):
    completions = None  # Initialize completions to None
    if request.method == 'POST':
        completions = generate_text(course_name)
        print(f"course_name: {course_name}")
        rendered = render_template('courses/course1.html', completions=completions, course_name=course_name)
        new_course = Course(course_name=course_name, content=rendered, user_id=current_user.id)
        db.session.add(new_course)
        db.session.commit()
        return rendered
    # If the request method is 'GET', generate the text for the course
    completions = generate_text(course_name)
    return render_template('courses/course1.html', completions=completions, course_name=course_name)


@app.route('/saved_course/<course_name>')
@login_required
def saved_course(course_name):
    course = Course.query.filter_by(course_name=course_name, user_id=current_user.id).first()
    if course is None:
        # If there is no course with the given name, redirect to the home page
        return "<p>Course not found</p>"
    else:
        # If a course with the given name exists, render a template and pass the course to it
        return render_template('courses/saved_course.html', course=course)




@app.route('/module/<course_name>/<module_name>', methods=['GET'])
def module(course_name,module_name):
    content = generate_module_content(course_name,module_name)
    if not content:
        return "<p>Module not found</p>"
    html = render_template('module.html', content=content)
    
    # If the 'download' query parameter is present in the URL, return the page as a PDF
    if 'download' in request.args:
        #Create a CSS object for the A3 page size
        a3_css = CSS(string='@page {size: A3; margin: 1cm;}')
        return render_pdf(HTML(string=html), stylesheets=[a3_css])

    # Otherwise, return the page as HTML
    return html 


@app.route('/app1')
def app1():
    if current_user.is_authenticated:
        saved_courses = Course.query.filter_by(user_id=current_user.id).all()
        recommended_courses = generate_recommendations(saved_courses)
        return render_template('app.html', saved_courses=saved_courses, recommended_courses = recommended_courses, user=current_user)
    else:
        return redirect(url_for('login'))

def markdown_to_list(markdown_string):
    # Split the string into lines
    lines = markdown_string.split('\n')
    # Use a regular expression to match lines that start with '* '
    list_items = [re.sub(r'\* ', '', line) for line in lines if line.startswith('* ')]
    return list_items

def generate_text(course):
    prompts = {
        'approach': f"""You are a pedagogy expert designing learning material for {course}.
            Describe the teaching approach and expected learning outcomes in bullet points.""",
        'modules': f"""List modules for {course} as bullet points (*).
            For each module: include a brief description after a colon."""
    }
    
    completions = {}
    generation_config = {
        "temperature": 0.1,
        "max_output_tokens": 5000,
    }
    
    # Generate approach content
    response = model.generate_content(
        prompts['approach'],
        generation_config=generation_config
    )
    completions['approach'] = markdown.markdown(response.text) if response.text else ""
    
    # Generate modules content
    response = model.generate_content(
        prompts['modules'],
        generation_config=generation_config
    )
    if response.text:
        markdown_string = response.text.replace('•', '*')
        completions['modules'] = markdown_to_list(markdown_string)
    else:
        completions['modules'] = []
    
    return completions

def generate_module_content(course_name, module_name):
    generation_config = {
        "temperature": 0.1,
        "max_output_tokens": 5000,
    }
    
    # Generate main content
    response = model.generate_content(
        f"Comprehensively explain {module_name} from {course_name} with examples/analogies.",
        generation_config=generation_config
    )
    module_content = md.convert(response.text) if response.text else ""
    
    # Generate code snippets
    code_response = model.generate_content(
        f"Provide code snippets for {module_name} in {course_name}.",
        generation_config=generation_config
    )
    code_content = md.convert(code_response.text) if code_response.text else ""
    
    # Generate ASCII diagrams
    ascii_response = model.generate_content(
        f"Create ASCII art diagrams explaining {module_name} in {course_name}.",
        generation_config=generation_config
    )
    ascii_content = md.convert(ascii_response.text) if ascii_response.text else ""
    
    return f"{module_content}\n{code_content}\n{ascii_content}"

def generate_recommendations(saved_courses):
    recommended_courses = []
    generation_config = {
        "temperature": 0.1,
        "max_output_tokens": 70,
    }
    
    for course in saved_courses:
        response = model.generate_content(
            f"Recommend one course to take after {course.course_name}. Format: 'Course Name: Description'",
            generation_config=generation_config
        )
        if response.text:
            parts = response.text.split(":", 1)
            if len(parts) == 2:
                name = parts[0].strip()
                desc = parts[1].strip()
                recommended_courses.append({
                    'name': name,
                    'description': markdown.markdown(desc)
                })
    
    return recommended_courses

@app.route('/about')
def about():
    return render_template('about.html')
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        users = User.query.all()
        print("\n📋 All Users in Database:")
        for user in users:
            print(f"ID: {user.id} | Username: {user.username} | Email: {user.email}")

        # 👇 DELETE USER BY ID (Change the ID to delete)
        user_id_to_delete = None  # e.g., 2

        if user_id_to_delete:
            user = db.session.get(User, user_id_to_delete)

            if user:
                print(f"❗Deleting user: {user.username} (ID: {user.id})")
                db.session.delete(user)
                db.session.commit()
                print("✅ User deleted successfully!")
            else:
                print(f"❌ No user found with ID {user_id_to_delete}")
    app.run(host="127.0.0.1", debug=True)
