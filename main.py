# ========= NIFTY BOT - FINAL v13 - DHANCONTEXT FIX - TOTP FIXED =========
import requests, csv, time, os, io, json, logging, tempfile
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
import pyotp
from dhanhq import DhanContext, dhanhq

# ============ CONFIG ============
LIVE_TRADING = os.getenv("LIVE_TRADING", "")
DRY_RUN = os.getenv("DRY_RUN", "True").lower() in ("true","1","yes") if "DRY_RUN" in os.environ else LIVE_TRADING!= "YES_I_AM_SURE"
TEST_MODE = os.getenv("TEST_MODE", "False").lower() in ("true","1","yes")
CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "1100000000")
CLIENT_PASSWORD = os.getenv("DHAN_PASSWORD", "")
TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET", "")
LOTS = int(os.getenv("LOTS", "1"))
BUDGET = int(os.getenv("BUDGET", "1500"))

IST = ZoneInfo("Asia/Kolkata")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
TOKEN_FILE = "dhan_token.txt"
TRADE_FILE = "active_trade.json"
TRADES_LOG = "trades_today.json"
JOURNAL = "journal.csv"
HOLIDAY_FILE = "nse_holidays.json"

logging.basicConfig(filename="bot.log", level=logging.INFO, format='%(asctime)s %(message)s')
def log(msg): print(msg); logging.info(msg)
def atomic_write(path,obj):
    with tempfile.NamedTemporaryFile('w',delete=False,dir='.') as tf:
        json.dump(obj,tf); tempname=tf.name
    os.replace(tempname,path)

def make_dhan(client_id, token):
    ctx = DhanContext(client_id if client_id else "1100000000", token)
    return dhanhq(ctx)

NSE_HOLIDAYS=set()
def load_holidays():
    global NSE_HOLIDAYS
    try:
        h={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
        s=requests.Session()
        s.get("https://www.nseindia.com/resources/exchange-communication-holidays", headers=h, timeout=10)
        time.sleep(1)
        data=s.get("https://www.nseindia.com/api/holiday-master?type=trading", headers=h, timeout=10).json()
        days=set()
        for item in data.get("FO",[]):
            try: days.add(str(datetime.strptime(item["tradingDate"],"%d-%b-%Y").date()))
            except: pass
        if days:
            NSE_HOLIDAYS=days
            atomic_write(HOLIDAY_FILE, sorted(days))
            log(f"Holidays live {len(days)}")
            return
    except Exception as e: log(f"Holiday fetch {e}")
    try:
        with open(HOLIDAY_FILE,"r") as f: NSE_HOLIDAYS=set(json.load(f))
        log(f"Holidays cache {len(NSE_HOLIDAYS)}")
    except: log("ALERT no holiday list")

def get_auto_token():
    # 1. Check valid file for today
    if os.path.exists(TOKEN_FILE):
        try:
            mtime_ist=datetime.fromtimestamp(os.path.getmtime(TOKEN_FILE),tz=IST).date()
            with open(TOKEN_FILE,"r") as f: tok=f.read().strip()
            if tok and mtime_ist==datetime.now(IST).date():
                log(f"Using token from file")
                return tok
        except: pass

    # 2. Try TOTP auto login - FIXED ENDPOINT
    try:
        if TOTP_SECRET and CLIENT_PASSWORD and CLIENT_ID:
            totp_code=pyotp.TOTP(TOTP_SECRET).now()
            # CORRECT URL as per Dhan docs
            url = f"https://auth.dhan.co/app/generateAccessToken?dhanClientId={CLIENT_ID}&pin={CLIENT_PASSWORD}&totp={totp_code}"
            log(f"[AUTH] Trying {CLIENT_ID} PIN {CLIENT_PASSWORD} TOTP {totp_code}")
            r=requests.post(url, timeout=15)
            log(f"[AUTH] Status {r.status_code} Body {r.text[:500]}")
            try:
                j=r.json()
            except:
                j={}
            token=j.get('accessToken') or j.get('access_token') or j.get('data',{}).get('jwtToken') or j.get('token')
            if token:
                with open(TOKEN_FILE,"w") as f: f.write(token)
                log(f"[AUTH] Generated fresh token expiry {j.get('expiryTime')}")
                return token
            else:
                log(f"Login failed {r.status_code} {r.text[:200]}")
        else:
            log("Need manual token or set CLIENT_ID/PASSWORD/TOTP_SECRET")
    except Exception as e:
        log(f"Auto login {e}")

    # 3. Fallback file
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE,"r") as f: return f.read().strip()
        except: pass
    return None

token=get_auto_token()
if not token: raise Exception("Token failed - paste into dhan_token.txt")
dhan=make_dhan(CLIENT_ID, token)

scrip_master, EXPIRY, LOT_SIZE = {}, None, 75
LAST_LOAD_DATE, EXPIRY_SWITCHED = None, False
load_holidays()

def load_scrip():
    global scrip_master, EXPIRY, LOT_SIZE
    try:
        text=requests.get(SCRIP_URL,timeout=20).text
        reader=list(csv.DictReader(io.StringIO(text)))
        opts=[]; today=datetime.now(IST).date()
        for r in reader:
            if r['SEM_EXM_EXCH_ID']=='NSE' and r['SEM_INSTRUMENT_NAME']=='OPTIDX' and r['SEM_TRADING_SYMBOL'].startswith('NIFTY-'):
                try:
                    exp=datetime.strptime(r['SEM_EXPIRY_DATE'][:10],'%Y-%m-%d').date()
                    if exp>=today: opts.append((exp,r))
                except: pass
        if not opts: return EXPIRY, LOT_SIZE
        nearest=min(e for e,_ in opts)
        if nearest==today and datetime.now(IST).hour>=13:
            nxt=[e for e,_ in opts if e>today]
            if nxt: nearest=min(nxt)
        new_master={}
        for exp,r in opts:
            if exp==nearest:
                key=(int(float(r['SEM_STRIKE_PRICE'])),r['SEM_OPTION_TYPE'])
                new_master[key]=(r['SEM_SMST_SECURITY_ID'],r['SEM_TRADING_SYMBOL'])
        if not new_master: return EXPIRY, LOT_SIZE
        scrip_master=new_master
        LOT_SIZE=int(float(next(r for e,r in opts if e==nearest)['SEM_LOT_UNITS']))
        EXPIRY=nearest
        log(f"EXPIRY {EXPIRY} LOT {LOT_SIZE} DRY_RUN={DRY_RUN}")
        return EXPIRY, LOT_SIZE
    except Exception as e:
        log(f"load_scrip {e}")
        return EXPIRY, LOT_SIZE

EXPIRY,LOT_SIZE=load_scrip()
while EXPIRY is None:
    time.sleep(120)
    EXPIRY,LOT_SIZE=load_scrip()
QTY=LOTS*LOT_SIZE

def get_sec_id(strike, opt_type): return scrip_master.get((int(strike),opt_type),(None,None))
def get_ltp(sec_id):
    try:
        data=dhan.quote_data(securities={"NSE_FNO":[int(sec_id)]})
        return float(data['data']['data']['NSE_FNO'][str(sec_id)]['last_price'])
    except: return 0

def get_quality_bias():
    try:
        h={"User-Agent":"Mozilla/5.0"}; s=requests.Session()
        s.get("https://www.nseindia.com/option-chain",headers=h,timeout=10); time.sleep(1)
        data=s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",headers=h,timeout=10).json()
        try:
            ts=datetime.strptime(data['records']['timestamp'],"%d-%b-%Y %H:%M:%S").date()
            if ts!=datetime.now(IST).date():
                log(f"Chain stale {ts}")
                spot=data['records']['underlyingValue']; atm=round(spot/50)*50
                return "NO_TRADE",atm,spot,data
        except: pass
        spot=data['records']['underlyingValue']; atm=round(spot/50)*50
        exp_str=EXPIRY.strftime('%d-%b-%Y'); ce_chg=pe_chg=ce_total=pe_total=0
        for item in data['records']['data']:
            if item.get('expiryDate')!=exp_str: continue
            if atm-250<=item['strikePrice']<=atm+250:
                if 'CE' in item: ce_total+=item['CE']['openInterest']; ce_chg+=item['CE']['changeinOpenInterest']
                if 'PE' in item: pe_total+=item['PE']['openInterest']; pe_chg+=item['PE']['changeinOpenInterest']
        total_chg=abs(pe_chg)+abs(ce_chg); diff_pct=(abs(pe_chg-ce_chg)/total_chg*100) if total_chg>0 else 0; pcr=pe_total/ce_total if ce_total>0 else 1
        log(f"Spot:{spot} Diff%:{diff_pct:.1f} PCR:{pcr:.2f} CE:{ce_chg} PE:{pe_chg}")
        if total_chg<50000 or diff_pct<30: return "NO_TRADE",atm,spot,data
        if pe_chg>ce_chg and pe_chg>0 and pcr>0.7: return "BULLISH",atm,spot,data
        if ce_chg>pe_chg and ce_chg>0 and pcr<1.4: return "BEARISH",atm,spot,data
        return "NO_TRADE",atm,spot,data
    except Exception as e:
        log(f"NSE {e}")
        return "NO_TRADE",22500,22500,None

def find_budget_strike(atm, opt_type, chain):
    exp_str=EXPIRY.strftime('%d-%b-%Y')
    strikes=[atm+i*50 for i in range(12)] if opt_type=="CE" else [atm-i*50 for i in range(12)]
    for strike in strikes:
        ltp_nse=0
        if chain:
            for item in chain['records']['data']:
                if item['strikePrice']==strike and item.get('expiryDate')==exp_str:
                    if opt_type=="CE" and 'CE' in item: ltp_nse=item['CE']['lastPrice']
                    if opt_type=="PE" and 'PE' in item: ltp_nse=item['PE']['lastPrice']
        if ltp_nse==0 or ltp_nse*QTY>BUDGET or ltp_nse<=2: continue
        sec_id,sym=get_sec_id(strike,opt_type)
        if not sec_id: continue
        dhan_ltp=get_ltp(sec_id)
        if dhan_ltp>0 and dhan_ltp*QTY<=BUDGET: return strike,sec_id,sym,dhan_ltp
    return None,None,None,None

def can_enter_today():
    try:
        if not os.path.exists(TRADES_LOG): return True
        with open(TRADES_LOG,"r") as f: data=json.load(f)
        if data.get('date')!=str(datetime.now(IST).date()): return True
        if data.get('count',0)>=3: return False
        if time.time()-data.get('last_exit',0)<600: return False
        return True
    except: return False

def log_exit_count():
    try:
        today=str(datetime.now(IST).date()); data={"date":today,"count":1,"last_exit":time.time()}
        if os.path.exists(TRADES_LOG):
            with open(TRADES_LOG,"r") as f: old=json.load(f)
            if old.get('date')==today: data['count']=old.get('count',0)+1
        with open(TRADES_LOG,'w') as f: json.dump(data,f)
    except: pass

def log_journal(entry_sym, entry_price, exit_price, qty, reason):
    try:
        pnl=(exit_price-entry_price)*qty
        row=f"{datetime.now(IST)},{entry_sym},{entry_price},{exit_price},{qty},{reason},{pnl:.0f}\n"
        exists=os.path.exists(JOURNAL)
        with open(JOURNAL,"a") as f:
            if not exists or os.path.getsize(JOURNAL)==0: f.write("time,symbol,entry,exit,qty,reason,pnl\n")
            f.write(row)
    except: pass

def get_order_status(order_id):
    try:
        od=dhan.get_order_by_id(order_id)
        d=od.get('data') if isinstance(od,dict) else od
        if isinstance(d,list): d=d[0] if d else {}
        if isinstance(d,dict) and 'data' in d: d=d['data']
        status=str(d.get('orderStatus') or d.get('status') or "").upper()
        avg=float(d.get('averageTradedPrice') or d.get('avgTradedPrice') or d.get('avgPrice') or 0)
        f_qty=int(d.get('filledQty') or d.get('tradedQuantity') or 0)
        return status,avg,f_qty,d
    except Exception as e:
        log(f"order_status {e}")
        return "UNKNOWN",0,0,{}

def get_position_qty(sec_id):
    try:
        pos=dhan.get_positions()
        for p in pos.get('data',[]):
            if str(p.get('securityId') or p.get('security_id'))==str(sec_id):
                return int(p.get('netQty') or p.get('net_qty') or 0)
        return 0
    except Exception as e:
        log(f"positions {e}")
        return None

def place_order_safe(sec_id, qty, side):
    if DRY_RUN:
        log(f"[DRY_RUN={DRY_RUN}] {side} {sec_id} {qty}")
        return {"ok":True,"order_id":"DRY","avg_price":0,"qty":qty}
    try:
        resp=dhan.place_order(security_id=int(sec_id),exchange_segment=dhan.NSE_FNO,transaction_type=dhan.BUY if side=="BUY" else dhan.SELL,quantity=qty,order_type=dhan.MARKET,product_type=dhan.INTRA,price=0)
        log(f"Place {side} {resp}")
        order_id=resp.get('data',{}).get('orderId') or resp.get('orderId')
        if not order_id: return {"ok":False}
        for _ in range(12):
            time.sleep(5)
            status,avg,f_qty,_ = get_order_status(order_id)
            log(f"Order {order_id} {status} avg {avg} qty {f_qty}")
            if status in ["TRADED","TRADED/CLOSED","COMPLETE","FILLED"]: return {"ok":True,"order_id":order_id,"avg_price":avg,"qty":f_qty if f_qty>0 else qty}
            if status in ["REJECTED","CANCELLED","FAILED"]: return {"ok":False}
        try:
            dhan.cancel_order(order_id); time.sleep(2)
            status,avg,f_qty,_ = get_order_status(order_id)
            if f_qty>0 or status in ["TRADED","COMPLETE","PART_TRADED","PARTIALLY_TRADED"]:
                return {"ok":True,"order_id":order_id,"avg_price":avg,"qty":f_qty if f_qty>0 else qty}
        except: pass
        return {"ok":False}
    except Exception as e:
        log(f"Place error {e}")
        return {"ok":False}

def place_broker_sl(sec_id, qty, entry_price):
    if DRY_RUN: return None, entry_price*0.70
    try:
        trigger=round(entry_price*0.70,1); limit_price=round(max(0.05,trigger-2.0),1)
        limit_price=round(limit_price*20)/20; trigger=round(trigger*20)/20
        resp=dhan.place_order(security_id=int(sec_id),exchange_segment=dhan.NSE_FNO,transaction_type=dhan.SELL,quantity=qty,order_type=dhan.STOP_LOSS,product_type=dhan.INTRA,price=limit_price,trigger_price=trigger)
        log(f"SL resp {resp}")
        sl_id=resp.get('data',{}).get('orderId') or resp.get('orderId')
        if sl_id: log(f"SL placed {sl_id} T {trigger} L {limit_price}")
        return sl_id, trigger
    except Exception as e:
        log(f"SL error {e}")
        return None, None

def modify_broker_sl(sl_order_id, new_trigger, new_limit):
    if DRY_RUN: return True
    try:
        new_trigger=round(new_trigger*20)/20
        new_limit=round(new_limit*20)/20
        resp=dhan.modify_order(order_id=sl_order_id, price=new_limit, trigger_price=new_trigger, order_type=dhan.STOP_LOSS)
        log(f"Modify SL {sl_order_id} -> T {new_trigger} L {new_limit} resp {resp}")
        time.sleep(1)
        status,_,_,_ = get_order_status(sl_order_id)
        if status in ["REJECTED","CANCELLED","EXPIRED","TRADED"]: return False
        return True
    except Exception as e:
        log(f"Modify error {e}")
        return False

def close_trade_safe(trade, reason):
    log(f"CLOSE {trade['symbol']} {reason} DRY={DRY_RUN}")
    if DRY_RUN:
        if os.path.exists(TRADE_FILE): os.remove(TRADE_FILE)
        log_exit_count(); log_journal(trade['symbol'],trade['entry'],get_ltp(trade['sec_id']),trade['qty'],reason)
        return True
    sl_id=trade.get('sl_order_id')
    if sl_id:
        status,avg,_,_ = get_order_status(sl_id)
        log(f"SL {sl_id} {status}")
        if status in ["TRADED","COMPLETE"]:
            if os.path.exists(TRADE_FILE): os.remove(TRADE_FILE)
            log_exit_count(); log_journal(trade['symbol'],trade['entry'],avg if avg>0 else trade['entry']*0.7,trade['qty'],f"SL_HIT_{reason}")
            return True
        try:
            dhan.cancel_order(sl_id)
            for _ in range(6):
                time.sleep(1)
                status,avg,_,_ = get_order_status(sl_id)
                if status in ["CANCELLED","CANCELLED_AFTER_MARKET","EXPIRED"]: break
                if status in ["TRADED","COMPLETE"]:
                    if os.path.exists(TRADE_FILE): os.remove(TRADE_FILE)
                    log_exit_count(); log_journal(trade['symbol'],trade['entry'],avg,trade['qty'],"SL_HIT")
                    return True
            if status not in ["CANCELLED","CANCELLED_AFTER_MARKET","EXPIRED"]: return False
        except: return False
    held=get_position_qty(trade['sec_id'])
    if held is None: return False
    if held<=0:
        if os.path.exists(TRADE_FILE): os.remove(TRADE_FILE)
        log_exit_count(); log_journal(trade['symbol'],trade['entry'],trade['entry'],trade['qty'],"EXTERNAL_EXIT")
        return True
    sell_qty=min(held,trade['qty'])
    res=place_order_safe(trade['sec_id'], sell_qty, "SELL")
    if res["ok"]:
        if os.path.exists(TRADE_FILE): os.remove(TRADE_FILE)
        log_exit_count(); log_journal(trade['symbol'],trade['entry'],res['avg_price'] if res['avg_price']>0 else get_ltp(trade['sec_id']),res['qty'],reason)
        return True
    else: return False

def handle_stale_trade_file():
    if not os.path.exists(TRADE_FILE): return
    try:
        with open(TRADE_FILE,"r") as f: trade=json.load(f)
        try: trade_date=datetime.fromisoformat(trade.get('time','')).date()
        except: trade_date=None
        today_str=str(datetime.now(IST).date())
        if trade_date and str(trade_date)!=today_str:
            log(f"Stale {trade_date} vs {today_str}")
            if DRY_RUN:
                log_journal(trade['symbol'],trade['entry'],get_ltp(trade['sec_id']),trade['qty'],"STALE")
                os.remove(TRADE_FILE)
                return
            else:
                held=get_position_qty(trade['sec_id'])
                if held==0:
                    log_journal(trade['symbol'],trade['entry'],trade['entry'],trade['qty'],"STALE_EXTERNAL_EXIT")
                    os.remove(TRADE_FILE)
                    return
                elif held is None: return
                else: return
        sl_id=trade.get('sl_order_id')
        if sl_id and not DRY_RUN:
            status,_,_,_ = get_order_status(sl_id)
            if status in ["EXPIRED"]:
                log(f"SL {sl_id} EXPIRED - clearing")
                trade['sl_order_id']=None
                atomic_write(TRADE_FILE, trade)
    except Exception as e: log(f"Stale check {e}")

LAST_LOAD_DATE=datetime.now(IST).date()
handle_stale_trade_file()
LAST_NSE_CALL, ltp_fail = 0, 0
log(f"START DRY_RUN:{DRY_RUN} TEST_MODE:{TEST_MODE} EXPIRY:{EXPIRY} QTY:{QTY}")

while True:
    try:
        now_ist=datetime.now(IST)
        if now_ist.date()!=LAST_LOAD_DATE and now_ist.time()>=dtime(8,45):
            log("08:45 reload")
            new_tok=get_auto_token()
            if new_tok is None:
                log("ALERT Token None"); time.sleep(120); continue
            dhan=make_dhan(CLIENT_ID,new_tok)
            load_holidays(); EXPIRY,LOT_SIZE=load_scrip(); QTY=LOTS*LOT_SIZE
            LAST_LOAD_DATE=now_ist.date(); EXPIRY_SWITCHED=False

        if EXPIRY==now_ist.date() and now_ist.hour>=13 and not EXPIRY_SWITCHED and not os.path.exists(TRADE_FILE):
            old=EXPIRY; EXPIRY,LOT_SIZE=load_scrip(); QTY=LOTS*LOT_SIZE; EXPIRY_SWITCHED=True
            log(f"13:00 switch {old}->{EXPIRY}")

        if EXPIRY==now_ist.date() and now_ist.hour>=13 and os.path.exists(TRADE_FILE):
            with open(TRADE_FILE,"r") as f: trade=json.load(f)
            close_trade_safe(trade,"EXPIRY_13:00")

        if EXPIRY is None:
            time.sleep(120); EXPIRY,LOT_SIZE=load_scrip(); continue

        if os.path.exists(TRADE_FILE):
            with open(TRADE_FILE,"r") as f: trade=json.load(f)
            if not trade.get('sl_order_id') and not DRY_RUN:
                sl_id, sl_trig = place_broker_sl(trade['sec_id'], trade['qty'], trade['entry'])
                if sl_id:
                    trade['sl_order_id']=sl_id; trade['curr_sl_trigger']=sl_trig; trade['high']=trade.get('high',trade['entry'])
                    atomic_write(TRADE_FILE, trade)
                else:
                    close_trade_safe(trade,"NO_SL_EMERGENCY"); time.sleep(5); continue
            sl_id=trade.get('sl_order_id')
            if sl_id and not DRY_RUN:
                s_status,s_avg,_,_ = get_order_status(sl_id)
                if s_status in ["TRADED","COMPLETE"]:
                    if os.path.exists(TRADE_FILE): os.remove(TRADE_FILE)
                    log_exit_count(); log_journal(trade['symbol'],trade['entry'],s_avg,trade['qty'],"SL_HIT")
                    time.sleep(5); continue
            ltp=get_ltp(trade['sec_id'])
            if ltp==0:
                ltp_fail+=1
                if ltp_fail>=6: close_trade_safe(trade,"LTP_FAIL"); ltp_fail=0
                time.sleep(5); continue
            ltp_fail=0
            if now_ist.time()>=dtime(15,10):
                close_trade_safe(trade,"15:10_SQ"); time.sleep(5); continue
            entry=trade['entry']; high=trade.get('high', entry); curr_sl=trade.get('curr_sl_trigger', entry*0.70)
            if ltp>high:
                high=ltp; trade['high']=high; new_sl=curr_sl
                if ltp>=entry*1.20 and curr_sl<entry: new_sl=entry
                if ltp>=entry*1.40 and curr_sl<entry*1.20: new_sl=entry*1.20
                if ltp>=entry*1.50:
                    trail_sl=high*0.75
                    if trail_sl>new_sl: new_sl=trail_sl
                if new_sl>curr_sl+0.05:
                    sl_id=trade.get('sl_order_id'); new_limit=round(max(0.05,new_sl-2.0),1)
                    if DRY_RUN or (sl_id and modify_broker_sl(sl_id, new_sl, new_limit)):
                        trade['curr_sl_trigger']=new_sl
                        log(f"TRAIL {entry}->{ltp:.1f} High {high:.1f} SL {curr_sl:.1f}->{new_sl:.1f}")
                    atomic_write(TRADE_FILE, trade)
                else:
                    atomic_write(TRADE_FILE, trade)
            log(f"MON {trade['symbol']} LTP:{ltp} High:{high:.1f} SL:{curr_sl:.1f} P&L:{(ltp-entry)*trade['qty']:.0f}")
            sl_to_check=trade.get('curr_sl_trigger', entry*0.70)
            if ltp<=sl_to_check:
                close_trade_safe(trade, f"TRAIL_SL_{sl_to_check:.1f}")
            elif ltp>=entry*1.60:
                close_trade_safe(trade,"TARGET")
            elif time.time()-LAST_NSE_CALL>60:
                LAST_NSE_CALL=time.time()
                curr_bias,_,_,_ = get_quality_bias()
                if curr_bias!="NO_TRADE" and curr_bias!=trade['bias']:
                    close_trade_safe(trade,f"REV {trade['bias']}->{curr_bias}")
            time.sleep(5)
        else:
            is_weekday=now_ist.weekday()<5; is_holiday=str(now_ist.date()) in NSE_HOLIDAYS
            market_open=is_weekday and not is_holiday and dtime(9,15)<=now_ist.time()<=dtime(15,15)
            can_new=is_weekday and not is_holiday and dtime(9,15)<=now_ist.time()<=dtime(14,45)
            if not market_open and not TEST_MODE: time.sleep(60); continue
            if not market_open and TEST_MODE:
                log(f"TEST API {now_ist.strftime('%H:%M:%S')} DRY:{DRY_RUN}")
                bias,atm,spot,chain=get_quality_bias()
                sec_id,sym=get_sec_id(atm,"CE")
                if sec_id: log(f"TEST OK {sym} {get_ltp(sec_id)}")
                time.sleep(60); continue
            if not can_new or not can_enter_today(): time.sleep(60); continue
            bias,atm,spot,chain=get_quality_bias(); LAST_NSE_CALL=time.time()
            if bias!="NO_TRADE":
                opt_type="CE" if bias=="BULLISH" else "PE"
                strike,sec_id,sym,ltp=find_budget_strike(atm,opt_type,chain)
                if sec_id:
                    result={"ok":True,"avg_price":ltp,"order_id":"DRY","qty":QTY} if DRY_RUN else place_order_safe(sec_id,QTY,"BUY")
                    if result["ok"]:
                        entry_price=result["avg_price"] if result["avg_price"]>0 else ltp
                        trade_obj={"sec_id":sec_id,"symbol":sym,"entry":entry_price,"bias":bias,"qty":result["qty"],"strike":strike,"time":str(now_ist),"sl_order_id":None,"curr_sl_trigger":entry_price*0.70,"high":entry_price,"buy_order_id":result["order_id"]}
                        atomic_write(TRADE_FILE, trade_obj)
                        log(f"BUY OK {sym} @ {entry_price} DRY={DRY_RUN}")
                        if not DRY_RUN:
                            sl_id, sl_trig = place_broker_sl(sec_id, result["qty"], entry_price)
                            if sl_id is None:
                                close_trade_safe(trade_obj,"NO_SL_AFTER_BUY")
                            else:
                                trade_obj["sl_order_id"]=sl_id
                                trade_obj["curr_sl_trigger"]=sl_trig
                                atomic_write(TRADE_FILE, trade_obj)
                    else: log(f"BUY FAIL {sym}")
                    time.sleep(5)
                else: time.sleep(60)
            else: time.sleep(60)
    except Exception as e:
        log(f"LOOP {e}"); time.sleep(10)