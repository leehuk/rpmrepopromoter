import json
import os
import subprocess
from flask import Flask, request, session, g, redirect, url_for, render_template
from flask_sqlalchemy import SQLAlchemy
from werkzeug.contrib.fixers import ProxyFix

app = Flask(__name__)
app.config.from_pyfile('config.cfg')

if 'REVERSE_PROXY' in app.config and app.config['REVERSE_PROXY']:
	app.wsgi_app = ProxyFix(app.wsgi_app)

if not 'USERNAME' in app.config or not 'PASSWORD' in app.config:
	raise Exception('Configuration Error (config.cfg): Missing USERNAME or PASSWORD option')
if not 'RPMREPODIFF' in app.config or not os.path.isfile(app.config['RPMREPODIFF']):
	raise Exception('Configuration Error (config.cfg): Missing or invalid RPMREPODIFF option')
if not 'PROMOTIONCMD' in app.config:
	raise Exception('Configuration Error (config.cfg): Missing PROMOTIONCMD option')
if not 'SQLALCHEMY_DATABASE_URI' in app.config:
	raise Exception('Configuration Error (config.cfg): Missing SQLALCHEMY_DATABASE_URI option')

db = SQLAlchemy(app)

menuitems = [
	('promotion', 'Promotion'),
	('repos', 'Manage Repos'),
	('flows', 'Manage Flows'),
]

@app.route('/')
def index():
	if not session.get('username'):
		return redirect(url_for('login'))

	return redirect(url_for('promotion'))

@app.route('/login', methods=['GET','POST'])
def login():
	if request.method == 'POST':
		if request.form['username'] == app.config['USERNAME'] and request.form['password'] == app.config['PASSWORD']:
			session['username'] = request.form['username']
			return redirect(url_for('index'))

	return render_template('login.html')

@app.route('/logout')
def logout():
	session['username'] = ''
	return redirect(url_for('login'))

@app.route('/promotion')
def promotion():
	flows = Flow.query.order_by('flowname').all()
	for flow in flows:
		flow.reposource = Repo.query.get(flow.flowsource)
		flow.repodest = Repo.query.get(flow.flowdest)

		qdiffcmd = subprocess.Popen([app.config['RPMREPODIFF'], '-s', flow.reposource.repourl, '-d', flow.repodest.repourl, '-b'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		stdout, stderr = qdiffcmd.communicate()
		retval = qdiffcmd.returncode

		if retval != 0:
			return render_template('error.html', menuitems=menuitems, error='Failed to run rpmrepodiff for ' + flow.flowname + ': ' + stderr.decode("utf-8"))

		diff = json.loads(stdout.decode("utf-8"))
		flow.synced = diff['synced']

	return render_template('promotion.html', menuitems=menuitems, flows=flows)

@app.route('/promotion/<int:flowid>')
def promotion_view(flowid):
	flow = Flow.query.get_or_404(flowid)
	flow.reposource = Repo.query.get(flow.flowsource)
	flow.repodest = Repo.query.get(flow.flowdest)

	diffcmd = subprocess.Popen([app.config['RPMREPODIFF'], '-s', flow.reposource.repourl, '-d', flow.repodest.repourl], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	stdout, stderr = diffcmd.communicate()
	retval = diffcmd.returncode

	if retval != 0:
		return render_template('error.html', menuitems=menuitems, error='Failed to run rpmrepodiff: ' + stderr.decode("utf-8"))

	rpmdiff = json.loads(stdout.decode("utf-8"))
	if len(rpmdiff) == 0:
		return render_template('promotion_insync.html', menuitems=menuitems, flow=flow)

	return render_template('promotion_view.html', menuitems=menuitems, flow=flow, rpmdiff=rpmdiff)

@app.route('/promotion/sync/<int:flowid>')
def promotion_sync(flowid):
	flow = Flow.query.get_or_404(flowid)
	flow.reposource = Repo.query.get(flow.flowsource)
	flow.repodest = Repo.query.get(flow.flowdest)

	if 'PROMOTIONCMD_QUOTE' in app.config and app.config['PROMOTIONCMD_QUOTE']:
		quotechar = "'"
	else:
		quotechar = ""

	promocmdstr = app.config['PROMOTIONCMD']
	promocmdstr = promocmdstr.replace('__FLOW_NAME__', quotechar + flow.flowname + quotechar)
	promocmdstr = promocmdstr.replace('__REPOSRC_NAME__', quotechar + flow.reposource.reponame + quotechar)
	promocmdstr = promocmdstr.replace('__REPOSRC_URL__', quotechar + flow.reposource.repourl + quotechar)
	promocmdstr = promocmdstr.replace('__REPODST_NAME__', quotechar + flow.repodest.reponame + quotechar)
	promocmdstr = promocmdstr.replace('__REPODST_URL__', quotechar + flow.repodest.repourl + quotechar)

	promocmd = subprocess.Popen(promocmdstr, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	stdout, stderr = promocmd.communicate()
	retval = promocmd.returncode

	if retval != 0:
		return render_template('error.html', menuitems=menuitems, error='Failed to run sync: ' + stderr.decode("utf-8"))

	return redirect(url_for('promotion'))

def repo_validate(form):
	if not form['reponame']:
		return 'Missing form field "reponame"'
	if not form['repourl']:
		return 'Missing form field "repourl"'

	if '"' in form['reponame'] or "'" in form['reponame']:
		return 'reponame cannot contain quote characters'
	if '"' in form['repourl'] or "'" in form['repourl']:
		return 'repourl cannot contain quote characters'

	return False

@app.route('/repos')
def repos():
	repos = Repo.query.order_by('reponame').all()
	return render_template('repos.html', menuitems=menuitems, repos=repos)

@app.route('/repo/add', methods=['GET','POST'])
def repo_add():
	if request.method == 'GET':
		return render_template('repo_add.html', menuitems=menuitems)

	error = repo_validate(request.form)
	if error:
		return render_template('error.html', menuitems=menuitems, error=error)

	repo = Repo(request.form['reponame'], request.form['repourl'])
	db.session.add(repo)
	db.session.commit()
	return redirect(url_for('repos'))

@app.route('/repo/edit/<int:repoid>', methods=['GET','POST'])
def repo_edit(repoid):
	repo = Repo.query.get_or_404(repoid)

	if request.method == 'GET':
		return render_template('repo_edit.html', menuitems=menuitems, repo=repo)

	error = repo_validate(request.form)
	if error:
		return render_template('error.html', menuitems=menuitems, error=error)

	repo.reponame = request.form['reponame']
	repo.repourl = request.form['repourl']

	db.session.add(repo)
	db.session.commit()

	return redirect(url_for('repos'))

@app.route('/repo/delete/<int:repoid>', methods=['POST'])
def repo_delete(repoid):
	repo = Repo.query.get_or_404(repoid)
	db.session.delete(repo)
	db.session.commit()
	return redirect(url_for('repos'))

def flow_validate(form):
	if not form['flowname']:
		return 'Missing form field "flowname"'
	if not form['flowsource']:
		return 'Missing form field "flowsource"'
	if not form['flowdest']:
		return 'Missing form field "flowdest"'

	if '"' in form['flowname'] or "'" in form['flowname']:
		return 'flowname cannot contain quote characters'
	if not Repo.query.get(form['flowsource']):
		return 'Invalid form field "flowsource"'
	if not Repo.query.get(form['flowdest']):
		return 'Invalid form field "flowdest"'

	return False

@app.route('/flows')
def flows():
	flows = Flow.query.order_by('flowname').all()
	for flow in flows:
		flow.reposource = Repo.query.get(flow.flowsource)
		flow.repodest = Repo.query.get(flow.flowdest)

	return render_template('flows.html', menuitems=menuitems, flows=flows)

@app.route('/flow/add', methods=['GET','POST'])
def flow_add():
	repos = Repo.query.order_by('reponame').all()

	if request.method == 'GET':
		return render_template('flow_add.html', menuitems=menuitems,repos=repos)

	error = flow_validate(request.form)
	if error:
		return render_template('error.html', menuitems=menuitems, error=error)

	flow = Flow(request.form['flowname'], request.form['flowsource'], request.form['flowdest'])
	db.session.add(flow)
	db.session.commit()
	return redirect(url_for('flows'))

@app.route('/flow/edit/<int:flowid>', methods=['GET','POST'])
def flow_edit(flowid):
	flow = Flow.query.get_or_404(flowid)
	repos = Repo.query.order_by('reponame').all()

	if request.method == 'GET':
		return render_template('flow_edit.html', menuitems=menuitems, flow=flow, repos=repos)

	error = flow_validate(request.form)
	if error:
		return render_template('error.html', menuitems=menuitems, error=error)

	flow.flowname = request.form['flowname']
	flow.flowsource = request.form['flowsource']
	flow.flowdest = request.form['flowdest']

	db.session.add(flow)
	db.session.commit()

	return redirect(url_for('flows'))

@app.route('/flow/delete/<int:flowid>', methods=['POST'])
def flow_delete(flowid):
	flow = Flow.query.get_or_404(flowid)
	db.session.delete(flow)
	db.session.commit()

	return redirect(url_for('flows'))

class Flow(db.Model):
	flowid = db.Column(db.Integer, primary_key=True)
	flowname = db.Column(db.Text, unique=True, nullable=False)
	flowsource = db.Column(db.Integer, db.ForeignKey('repo.repoid', ondelete="CASCADE"))
	flowdest = db.Column(db.Integer, db.ForeignKey('repo.repoid', ondelete="CASCADE"))

	def __init__(self, name, source, dest):
		self.flowname = name
		self.flowsource = source
		self.flowdest = dest

class Repo(db.Model):
	repoid = db.Column(db.Integer, primary_key=True)
	reponame = db.Column(db.Text, unique=True, nullable=False)
	repourl = db.Column(db.Text, nullable=False)

	def __init__(self, name, url):
		self.reponame = name
		self.repourl = url
