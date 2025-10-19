# app.py
from flask import Flask, render_template, request, redirect, session, jsonify, flash
from functools import wraps
import os
import json
import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import yfinance as yf
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
import logging

logging.getLogger('yfinance').setLevel(logging.CRITICAL)

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this to a random secret key

DATA_DIR = 'data/users'
SERVER_DATA = 'data/server.json'
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs('data', exist_ok=True)

# Fetch available assets
def get_sp500():
    url = "https://www.slickcharts.com/sp500"
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get(url, headers=headers)
    soup = BeautifulSoup(resp.text, 'html.parser')
    table = soup.find('table', class_='table')
    stocks = []
    if table:
        for tr in table.find_all('tr')[1:]:
            tds = tr.find_all('td')
            if len(tds) > 3:
                name = tds[1].text.strip()
                symbol = tds[2].text.strip()
                stocks.append({"name": name, "symbol": symbol})
    return stocks

def get_commodities():
    return [
        {"name": "Gold", "symbol": "GC=F"},
        {"name": "Silver", "symbol": "SI=F"},
        {"name": "Crude Oil", "symbol": "CL=F"},
        {"name": "Brent Crude", "symbol": "BZ=F"},
        {"name": "Natural Gas", "symbol": "NG=F"},
        {"name": "Gasoline", "symbol": "RB=F"},
        {"name": "Heating Oil", "symbol": "HO=F"},
        {"name": "Copper", "symbol": "HG=F"},
        {"name": "Platinum", "symbol": "PL=F"},
        {"name": "Palladium", "symbol": "PA=F"},
        {"name": "Corn", "symbol": "ZC=F"},
        {"name": "Soybeans", "symbol": "ZS=F"},
        {"name": "Wheat", "symbol": "ZW=F"},
        {"name": "Coffee", "symbol": "KC=F"},
        {"name": "Cocoa", "symbol": "CC=F"},
        {"name": "Sugar", "symbol": "SB=F"},
        {"name": "Cotton", "symbol": "CT=F"},
        {"name": "Live Cattle", "symbol": "LE=F"},
        {"name": "Lean Hogs", "symbol": "HE=F"},
        {"name": "Feeder Cattle", "symbol": "GF=F"},
        {"name": "Lumber", "symbol": "LB=F"},
    ]

assets = {"stocks": get_sp500(), "commodities": get_commodities()}
all_assets = assets["stocks"] + assets["commodities"]

def get_asset_name(symbol):
    for a in all_assets:
        if a["symbol"] == symbol:
            return a["name"]
    return symbol

# User functions
def get_user_path(username):
    return os.path.join(DATA_DIR, f"{username.lower()}.json")

def load_user(username):
    path = get_user_path(username)
    if os.path.exists(path):
        with open(path, 'r') as f:
            data = json.load(f)
            if 'start_date' in data and isinstance(data['start_date'], str):
                data['start_date'] = datetime.datetime.fromisoformat(data['start_date'])
            if 'portfolio' not in data:
                data['portfolio'] = {'long': {}, 'short': {}}
            if 'transactions' not in data:
                data['transactions'] = []
            if 'commission_rate' not in data:
                data['commission_rate'] = 0.00005  # Default for old users
            return data
    return None

def save_user(username, data):
    if 'start_date' in data and isinstance(data['start_date'], datetime.datetime):
        data['start_date'] = data['start_date'].isoformat()
    path = get_user_path(username)
    with open(path, 'w') as f:
        json.dump(data, f)

def user_exists(username):
    return load_user(username) is not None

def get_current_price(symbol):
    ticker = yf.Ticker(symbol)
    return ticker.info.get('currentPrice', ticker.info.get('regularMarketPrice', 0.0))

def get_historical_close(symbol, dt):
    ticker = yf.Ticker(symbol)
    # Try to get around dt
    start = dt - datetime.timedelta(hours=12)  # larger window
    end = dt + datetime.timedelta(minutes=5)
    hist = ticker.history(start=start, end=end, interval='5m')
    if not hist.empty:
        hist = hist[hist.index.tz_convert(None) <= dt]
        if not hist.empty:
            return hist['Close'].iloc[-1]
    # If no intra, fall to daily last before
    hist = ticker.history(period="1y")
    hist = hist[hist.index.tz_convert(None) <= dt]
    if not hist.empty:
        return hist['Close'].iloc[-1]
    return get_current_price(symbol)  # if future or no data

def is_triggered(typ, price, pos):
    stop_loss = pos.get('stop_loss')
    stop_profit = pos.get('stop_profit')
    if typ == 'long':
        if stop_loss is not None and price <= stop_loss:
            return True, 'stop_loss'
        if stop_profit is not None and price >= stop_profit:
            return True, 'stop_profit'
    elif typ == 'short':
        if stop_loss is not None and price >= stop_loss:
            return True, 'stop_loss'
        if stop_profit is not None and price <= stop_profit:
            return True, 'stop_profit'
    return False, None

def perform_auto_close(username, typ, symbol, price, action_type, dt=None):
    user = load_user(username)
    if dt is None:
        dt = datetime.datetime.now()
    pos = user['portfolio'][typ][symbol]
    amount = pos['amount']
    commission_rate = user['commission_rate']
    commission = amount * price * commission_rate
    if typ == 'long':
        user['current_balance'] += amount * price - commission
    else:
        user['current_balance'] += amount * (2 * pos['avg_price'] - price) - commission
    del user['portfolio'][typ][symbol]
    tx = {'datetime': dt.isoformat(), 'action': action_type, 'symbol': symbol, 'amount': amount, 'price': price, 'commission': commission}
    user['transactions'].append(tx)
    save_user(username, user)

def check_positions():
    users = [f[:-5] for f in os.listdir(DATA_DIR) if f.endswith('.json')]
    for username in users:
        user = load_user(username)
        for typ in ['long', 'short']:
            for symbol in list(user['portfolio'][typ]):
                pos = user['portfolio'][typ][symbol]
                if 'stop_loss' in pos or 'stop_profit' in pos:
                    price = get_current_price(symbol)
                    triggered, action_type = is_triggered(typ, price, pos)
                    if triggered:
                        perform_auto_close(username, typ, symbol, price, action_type)
    # Update last check
    with open(SERVER_DATA, 'w') as f:
        json.dump({'last_check': datetime.datetime.now().isoformat()}, f)

def catch_up():
    if os.path.exists(SERVER_DATA):
        with open(SERVER_DATA, 'r') as f:
            data = json.load(f)
            last_check_str = data.get('last_check')
            if last_check_str:
                last_check = datetime.datetime.fromisoformat(last_check_str)
                now = datetime.datetime.now()
                times = []
                ct = last_check + datetime.timedelta(minutes=10)
                while ct < now:
                    times.append(ct)
                    ct += datetime.timedelta(minutes=10)
                users = [f[:-5] for f in os.listdir(DATA_DIR) if f.endswith('.json')]
                for t in times:
                    for username in users:
                        user = load_user(username)
                        for typ in ['long', 'short']:
                            for symbol in list(user['portfolio'][typ]):
                                pos = user['portfolio'][typ][symbol]
                                if 'stop_loss' in pos or 'stop_profit' in pos:
                                    price = get_historical_close(symbol, t)
                                    if price is not None:
                                        triggered, action_type = is_triggered(typ, price, pos)
                                        if triggered:
                                            perform_auto_close(username, typ, symbol, price, action_type, dt=t)

@app.route('/')
def index():
    if 'username' in session:
        return redirect('/account')
    return render_template('index.html')

@app.route('/account/create', methods=['GET', 'POST'])
def create_account():
    if request.method == 'POST':
        username = request.form['username'].lower()
        password = request.form['password']
        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return render_template('create_account.html')
        if user_exists(username):
            flash("Username already exists.")
            return render_template('create_account.html')
        hash_pw = generate_password_hash(password)
        try:
            initial_balance = float(request.form['initial_balance'])
            if initial_balance < 40000:
                flash("Initial balance must be at least $40,000.")
                return render_template('create_account.html')
            commission_rate = float(request.form.get('commission_rate', 0.005)) / 100
            if commission_rate < 0:
                flash("Commission rate must be at least 0%.")
                return render_template('create_account.html')
        except ValueError:
            flash("Invalid initial balance or commission rate.")
            return render_template('create_account.html')
        data = {
            'password': hash_pw,
            'initial_balance': initial_balance,
            'current_balance': initial_balance,
            'commission_rate': commission_rate,
            'portfolio': {'long': {}, 'short': {}},
            'transactions': [],
            'start_date': datetime.datetime.now()
        }
        save_user(username, data)
        session['username'] = username
        return redirect('/account')
    return render_template('create_account.html')

@app.route('/account/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].lower()
        password = request.form['password']
        user = load_user(username)
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            return redirect('/account')
        flash("Invalid username or password.")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect('/')

def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'username' in session:
            return f(*args, **kwargs)
        return redirect('/account/login')
    return wrap

def get_portfolio_value_at_date(username, target_date):
    user = load_user(username)
    balance = user['initial_balance']
    commission_rate = user['commission_rate']
    long = {}
    short = {}
    transactions = sorted(user['transactions'], key=lambda x: datetime.datetime.fromisoformat(x['datetime']))
    for tx in transactions:
        tx_dt = datetime.datetime.fromisoformat(tx['datetime'])
        if tx_dt.date() > target_date:
            break
        symbol = tx['symbol']
        am = tx['amount']
        pr = tx['price']
        act = tx['action']
        comm = am * pr * commission_rate
        if act == 'buy':
            if symbol in long:
                old_am = long[symbol]['amount']
                old_pr = long[symbol]['avg_price']
                new_am = old_am + am
                new_pr = (old_am * old_pr + am * pr) / new_am
                long[symbol]['avg_price'] = new_pr
                long[symbol]['amount'] = new_am
            else:
                long[symbol] = {'amount': am, 'avg_price': pr}
            balance -= am * pr + comm
        elif act == 'short':
            if symbol in short:
                old_am = short[symbol]['amount']
                old_pr = short[symbol]['avg_price']
                new_am = old_am + am
                new_pr = (old_am * old_pr + am * pr) / new_am
                short[symbol]['avg_price'] = new_pr
                short[symbol]['amount'] = new_am
            else:
                short[symbol] = {'amount': am, 'avg_price': pr}
            balance -= am * pr + comm
        elif act in ['sell_cover', 'stop_loss', 'stop_profit']:
            revenue = 0
            if symbol in long:
                long_am = long.get(symbol, {}).get('amount', 0)
                revenue = am * pr
                if am >= long_am:
                    long.pop(symbol, None)
                else:
                    long[symbol]['amount'] -= am
            elif symbol in short:
                short_am = short.get(symbol, {}).get('amount', 0)
                avg = short.get(symbol, {}).get('avg_price', pr) if symbol in short else pr
                revenue = am * (2 * avg - pr)
                if am >= short_am:
                    short.pop(symbol, None)
                else:
                    short[symbol]['amount'] -= am
            balance += revenue - comm
    value = balance
    for symbol, pos in long.items():
        close = get_historical_close(symbol, datetime.datetime.combine(target_date, datetime.time(0,0)))
        if close is None:
            close = pos['avg_price']
        value += pos['amount'] * close
    for symbol, pos in short.items():
        close = get_historical_close(symbol, datetime.datetime.combine(target_date, datetime.time(0,0)))
        if close is None:
            close = pos['avg_price']
        value += pos['amount'] * (2 * pos['avg_price'] - close)
    return value

@app.route('/account')
@login_required
def account():
    username = session['username']
    user = load_user(username)
    asset_values = []
    total_assets = 0.0
    current_balance = user['current_balance']
    for typ in ['long', 'short']:
        for symbol, pos in user['portfolio'].get(typ, {}).items():
            ticker = yf.Ticker(symbol)
            price = ticker.info.get('currentPrice', ticker.info.get('regularMarketPrice', 0.0))
            if typ == 'long':
                value = pos['amount'] * price
                percent = ((price - pos['avg_price']) / pos['avg_price'] * 100) if pos['avg_price'] > 0 else 0.0
            else:
                value = pos['amount'] * (2 * pos['avg_price'] - price)
                percent = - ((price - pos['avg_price']) / pos['avg_price'] * 100) if pos['avg_price'] > 0 else 0.0
            asset_values.append({
                'symbol': symbol,
                'name': get_asset_name(symbol),
                'amount': pos['amount'],
                'price': price,
                'value': value,
                'action': 'Long' if typ == 'long' else 'Short',
                'percent_change': percent
            })
            total_assets += value
    total_value = current_balance + total_assets
    initial_value = user['initial_balance']
    percent_change = ((total_value / initial_value) - 1) * 100 if initial_value > 0 else 0.0
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    value_yesterday = get_portfolio_value_at_date(username, yesterday)
    day_percent_change = ((total_value / value_yesterday) - 1) * 100 if value_yesterday > 0 else 0.0
    return render_template('account.html', balance=current_balance, asset_values=asset_values, total_value=total_value, percent_change=percent_change, day_percent_change=day_percent_change)

@app.route('/account/history')
@login_required
def history():
    username = session['username']
    user = load_user(username)
    start_date = user['start_date'].date()
    today = datetime.date.today()
    days = []
    previous_value = user['initial_balance']
    for n in range(0, (today - start_date).days + 1):
        day = start_date + datetime.timedelta(days=n)
        value = get_portfolio_value_at_date(username, day)
        change = ((value / previous_value) - 1) * 100 if previous_value > 0 else 0.0
        days.append({'day': day.strftime('%Y-%m-%d'), 'value': value, 'change': change})
        previous_value = value
    days_table = days[::-1]
    days_chart = [d['day'] for d in days]
    values_chart = [d['value'] for d in days]
    transactions = sorted(user['transactions'], key=lambda x: x['datetime'], reverse=True)
    return render_template('history.html', days=days_table, days_chart=days_chart, values_chart=values_chart, transactions=transactions)

@app.route('/stats/<symbol>')
@login_required
def stats(symbol):
    if not any(a['symbol'] == symbol for a in all_assets):
        flash("Invalid asset.")
        return redirect('/account')
    ticker = yf.Ticker(symbol)
    info = ticker.info
    name = info.get('longName', get_asset_name(symbol))
    current_price = info.get('currentPrice', info.get('regularMarketPrice', 0.0))
    return render_template('stats.html', symbol=symbol, name=name, price=current_price)

@app.route('/api/history/<symbol>/<period>')
@login_required
def api_history(symbol, period):
    periods_map = {'1d': '1d', '7d': '7d', '1m': '1mo', '3m': '3mo', '6m': '6mo', '1y': '1y'}
    p = periods_map.get(period, '1y')
    interval = '1d' if p in ['1y', '6mo', '3mo', '1mo'] else '5m' if p == '1d' else '1h' if p == '7d' else '1d'
    hist = yf.Ticker(symbol).history(period=p, interval=interval)
    dates = hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist()
    prices = hist['Close'].tolist()
    return jsonify({'dates': dates, 'prices': prices})

@app.route('/buy/<symbol>', methods=['POST'])
@login_required
def buy(symbol):
    username = session['username']
    user = load_user(username)
    try:
        amount = int(request.form['amount'])
        if amount < 1:
            raise ValueError
        stop_loss_str = request.form.get('stop_loss', '')
        stop_profit_str = request.form.get('stop_profit', '')
        stop_loss = float(stop_loss_str) if stop_loss_str else None
        stop_profit = float(stop_profit_str) if stop_profit_str else None
    except ValueError:
        flash("Invalid amount or stop levels.")
        return redirect(f'/stats/{symbol}')
    price = get_current_price(symbol)
    cost = amount * price
    commission_rate = user['commission_rate']
    commission = cost * commission_rate
    total_cost = cost + commission
    if user['current_balance'] < total_cost:
        flash("Insufficient balance.")
        return redirect(f'/stats/{symbol}')
    user['current_balance'] -= total_cost
    if symbol in user['portfolio']['long']:
        pos = user['portfolio']['long'][symbol]
        old_am = pos['amount']
        old_pr = pos['avg_price']
        new_am = old_am + amount
        new_pr = (old_am * old_pr + amount * price) / new_am
        pos['avg_price'] = new_pr
        pos['amount'] = new_am
    else:
        user['portfolio']['long'][symbol] = {'amount': amount, 'avg_price': price}
    user['portfolio']['long'][symbol]['stop_loss'] = stop_loss
    user['portfolio']['long'][symbol]['stop_profit'] = stop_profit
    tx = {'datetime': datetime.datetime.now().isoformat(), 'action': 'buy', 'symbol': symbol, 'amount': amount, 'price': price, 'commission': commission}
    user['transactions'].append(tx)
    save_user(username, user)
    flash("Buy successful.")
    return redirect(f'/stats/{symbol}')

@app.route('/short/<symbol>', methods=['POST'])
@login_required
def short(symbol):
    username = session['username']
    user = load_user(username)
    try:
        amount = int(request.form['amount'])
        if amount < 1:
            raise ValueError
        stop_loss_str = request.form.get('stop_loss', '')
        stop_profit_str = request.form.get('stop_profit', '')
        stop_loss = float(stop_loss_str) if stop_loss_str else None
        stop_profit = float(stop_profit_str) if stop_profit_str else None
    except ValueError:
        flash("Invalid amount or stop levels.")
        return redirect(f'/stats/{symbol}')
    price = get_current_price(symbol)
    cost = amount * price
    commission_rate = user['commission_rate']
    commission = cost * commission_rate
    total_cost = cost + commission
    if user['current_balance'] < total_cost:
        flash("Insufficient balance.")
        return redirect(f'/stats/{symbol}')
    user['current_balance'] -= total_cost
    if symbol in user['portfolio']['short']:
        pos = user['portfolio']['short'][symbol]
        old_am = pos['amount']
        old_pr = pos['avg_price']
        new_am = old_am + amount
        new_pr = (old_am * old_pr + amount * price) / new_am
        pos['avg_price'] = new_pr
        pos['amount'] = new_am
    else:
        user['portfolio']['short'][symbol] = {'amount': amount, 'avg_price': price}
    user['portfolio']['short'][symbol]['stop_loss'] = stop_loss
    user['portfolio']['short'][symbol]['stop_profit'] = stop_profit
    tx = {'datetime': datetime.datetime.now().isoformat(), 'action': 'short', 'symbol': symbol, 'amount': amount, 'price': price, 'commission': commission}
    user['transactions'].append(tx)
    save_user(username, user)
    flash("Short successful.")
    return redirect(f'/stats/{symbol}')

@app.route('/sell_cover/<symbol>', methods=['POST'])
@login_required
def sell_cover(symbol):
    username = session['username']
    user = load_user(username)
    try:
        amount = int(request.form['amount'])
        if amount < 1:
            raise ValueError
    except ValueError:
        flash("Invalid amount.")
        return redirect(f'/stats/{symbol}')
    price = get_current_price(symbol)
    commission_rate = user['commission_rate']
    commission = amount * price * commission_rate
    found = False
    revenue = 0
    if symbol in user['portfolio']['long']:
        pos = user['portfolio']['long'][symbol]
        if pos['amount'] < amount:
            flash("Insufficient amount in portfolio.")
            return redirect(f'/stats/{symbol}')
        revenue = amount * price
        pos['amount'] -= amount
        if pos['amount'] == 0:
            del user['portfolio']['long'][symbol]
        found = True
    elif symbol in user['portfolio']['short']:
        pos = user['portfolio']['short'][symbol]
        if pos['amount'] < amount:
            flash("Insufficient amount in portfolio.")
            return redirect(f'/stats/{symbol}')
        revenue = amount * (2 * pos['avg_price'] - price)
        pos['amount'] -= amount
        if pos['amount'] == 0:
            del user['portfolio']['short'][symbol]
        found = True
    if not found:
        flash("No position to sell/cover.")
        return redirect(f'/stats/{symbol}')
    user['current_balance'] += revenue - commission
    tx = {'datetime': datetime.datetime.now().isoformat(), 'action': 'sell_cover', 'symbol': symbol, 'amount': amount, 'price': price, 'commission': commission}
    user['transactions'].append(tx)
    save_user(username, user)
    flash("Sell/Cover successful.")
    return redirect(f'/stats/{symbol}')

@app.route('/search', methods=['POST'])
@login_required
def search():
    query = request.form.get('query', '').lower()
    results = [a for a in all_assets if query in a['name'].lower() or query in a['symbol'].lower()]
    return render_template('search.html', results=results)

def datetimeformat(value):
    return datetime.datetime.fromisoformat(value).strftime('%Y %b %d %H:%M:%S')

app.jinja_env.filters['datetimeformat'] = datetimeformat

scheduler = BackgroundScheduler()
scheduler.add_job(func=check_positions, trigger="interval", minutes=10)
scheduler.start()

if __name__ == '__main__':
    catch_up()
    app.run(debug=True)
