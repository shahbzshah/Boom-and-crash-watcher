"""Crash Watch dashboard page, served by health.py at GET /.

The markup and styling implement Megah's Stitch design, "Signal
Room" (deep petrol ink, flat tier colours — amber CRT, jade
confirmed, violet shadow, coral stops, sky live — Newsreader serif +
Space Mono + DM Sans, zero-radius instrument panels). All data comes
from /status.json (+ /candles, fetched once per page load, for chart
history), which the watcher builds from its real state — nothing on
this page is decorative filler. The page itself holds no data, so it
is safe to serve openly; the data endpoints are key-gated (see
health.py).
"""

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crash Watch — Crash 1000 Alert Bot</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#0B1210; --panel:#12201B; --panel2:#0E1A16; --panel3:#162822;
  --hair:#1C332B; --text:#C6EBDB; --paper:#F2F7F5;
  --muted:#94ADA2; --faint:#638578;
  --amber:#FFB224; --jade:#2FCE8F; --violet:#9B8CFF;
  --coral:#FF6A5E; --sky:#45C4F5;
  --green:#2FCE8F; --red:#FF6A5E; --rust:#FFB224;
  --serif:"Newsreader",Georgia,"Times New Roman",serif;
  --mono:"Space Mono",ui-monospace,"Courier New",monospace;
  --sans:"DM Sans",system-ui,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--ink)}
body{background:var(--ink);color:var(--text);font-family:var(--mono);
  font-size:13px;line-height:1.45;-webkit-font-smoothing:antialiased}
.wrap{max-width:430px;margin:0 auto;padding:0 12px 96px}
a{color:var(--text)}
/* header */
header{position:sticky;top:0;z-index:20;background:var(--ink);
  border-bottom:1px solid var(--hair)}
.hd{max-width:430px;margin:0 auto;padding:12px;display:flex;
  align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;
  letter-spacing:.14em;font-size:13px}
.brand .sq{width:9px;height:9px;background:var(--coral);flex:none}
.pill{display:flex;align-items:center;gap:7px;border:1px solid var(--hair);
  background:var(--panel);border-radius:0;padding:5px 11px;
  font-size:10px;font-weight:700;letter-spacing:.12em}
.dot{width:7px;height:7px;border-radius:50%;background:var(--faint);flex:none}
.dot.live{background:var(--sky);animation:pulse 2s infinite}
.dot.down{background:var(--red)}
@keyframes pulse{0%{opacity:1}50%{opacity:.35}100%{opacity:1}}
.pill .clk{color:var(--muted);font-weight:400;letter-spacing:.04em}
/* generic */
.card{background:var(--panel);border:1px solid var(--hair);
  border-radius:0;padding:14px;margin-top:12px}
.eyebrow{font-size:10px;font-weight:700;letter-spacing:.14em;
  color:var(--muted);text-transform:uppercase}
.serif{font-family:var(--serif)}
.mut{color:var(--muted)} .grn{color:var(--green)} .red{color:var(--red)}
.hr{border:none;border-top:1px solid var(--hair);margin:10px 0}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
/* hero */
.price{font-family:var(--serif);font-size:44px;line-height:1.05;
  letter-spacing:-.01em;margin:6px 0 2px;color:var(--amber)}
.chg{text-align:right;font-size:12px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.chip{border:1px solid var(--hair);background:var(--panel2);border-radius:0;
  padding:4px 9px;font-size:10px;font-weight:700;letter-spacing:.1em}
.foot{font-size:10px;color:var(--muted);letter-spacing:.03em;margin-top:10px;
  border-top:1px solid var(--hair);padding-top:9px}
/* chart */
.chartbox{position:relative;margin-top:8px}
canvas{width:100%;height:170px;display:block;touch-action:none;cursor:grab}
canvas:active{cursor:grabbing}
.zbtns{display:flex;align-items:center;gap:6px}
.zbtns button{background:var(--panel2);border:1px solid var(--hair);color:var(--text);
  font-family:var(--mono);font-size:15px;font-weight:700;line-height:1;
  width:26px;height:26px;cursor:pointer;padding:0;flex:none}
.zbtns button:active{background:var(--panel3);color:var(--amber)}
.liqtag{position:absolute;right:0;background:var(--ink);
  border:1px solid var(--hair);color:var(--violet);font-size:9px;
  padding:1px 5px;letter-spacing:.05em;transform:translateY(-50%)}
.strip{display:flex;justify-content:space-between;border-top:1px solid var(--hair);
  padding-top:9px;margin-top:9px;font-size:11px;color:var(--muted)}
.strip b{color:var(--text);font-weight:400}
/* score */
.big{font-size:32px;font-weight:700;letter-spacing:-.02em}
.big span{color:var(--faint);font-weight:400;font-size:22px}
.cond{display:flex;align-items:center;gap:10px;padding:9px 0;
  border-top:1px solid var(--hair)}
.cond:first-of-type{border-top:none}
.cond .mark{width:16px;flex:none;font-weight:700}
.cond .lab{flex:1}
.cond .val{color:var(--muted);font-size:12px;text-align:right}
.cond .val b{color:var(--text);font-weight:400}
.gauge{height:2px;background:var(--hair);margin-top:12px}
.gauge i{display:block;height:100%;background:var(--amber)}
/* signal */
.sig{border-left:3px solid var(--amber)}
.sig.buy{border-left-color:var(--jade)}
.sigtitle{font-family:var(--serif);font-size:19px;font-weight:500;margin:4px 0 6px}
.figures{font-size:13px}
.tag{display:inline-block;border:1px solid var(--amber);color:var(--amber);
  border-radius:0;padding:0 6px;font-size:10px;font-weight:700;
  letter-spacing:.08em;vertical-align:1px}
.tag.buy{border-color:var(--jade);color:var(--jade)}
.outcome{font-size:11px;font-weight:700;letter-spacing:.06em}
/* history */
.hrow{padding:11px 0;border-top:1px solid var(--hair);cursor:pointer}
.hrow:first-of-type{border-top:none}
.hmain{display:flex;align-items:center;gap:9px}
.hmain .tm{width:44px;flex:none;font-weight:700}
.hmain .en{flex:1;font-size:14px}
.hdetail{display:none;margin-top:10px;background:var(--panel2);
  border:1px solid var(--hair);border-radius:0;padding:10px}
.hrow.open .hdetail{display:block}
.sltp{display:flex;gap:18px;font-size:12px;color:var(--muted);flex-wrap:wrap}
.sltp b{color:var(--text);font-weight:400}
.condmini{margin-top:8px;border-top:1px solid var(--hair);padding-top:8px}
.condmini div{display:flex;gap:8px;padding:2px 0;font-size:12px}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:0}
.stats .cell{padding:10px 2px}
.stats .num{font-size:27px;font-weight:700;letter-spacing:-.02em}
.stats .num small{font-size:13px;color:var(--muted);font-weight:400}
.fchips{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0 2px}
.fchip{border:1px solid var(--hair);background:var(--panel);color:var(--muted);
  border-radius:0;padding:5px 10px;font-size:10px;font-weight:700;
  letter-spacing:.09em;cursor:pointer;font-family:var(--mono)}
.fchip.on{color:var(--paper);border-color:var(--amber)}
.daylab{display:flex;justify-content:space-between;margin:16px 0 2px}
/* config */
.kv{display:flex;justify-content:space-between;gap:14px;padding:8px 0;
  border-top:1px solid var(--hair);font-size:12px}
.kv:first-of-type{border-top:none}
.kv .k{color:var(--muted)}
.kv .v{text-align:right;overflow-wrap:break-word}
.note{background:var(--panel2);border:1px solid var(--hair);border-radius:0;
  padding:10px;font-size:11px;color:var(--muted);margin-top:12px}
.sans{font-family:var(--sans)}
.btn{display:block;width:100%;margin-top:12px;background:var(--amber);
  color:#0B1210;border:none;border-radius:0;padding:14px;
  font-family:var(--mono);font-size:12px;font-weight:700;
  letter-spacing:.12em;cursor:pointer;text-align:left}
.btn small{display:block;font-weight:400;letter-spacing:.02em;
  color:#614000;font-size:10px;margin-top:2px}
.btn:disabled{opacity:.55}
/* nav */
nav{position:fixed;left:0;right:0;bottom:0;background:var(--ink);
  border-top:1px solid var(--hair);z-index:20}
.navin{max-width:430px;margin:0 auto;display:flex}
.navin button{flex:1;background:none;border:none;border-top:2px solid transparent;color:var(--faint);
  font-family:var(--mono);font-size:9px;font-weight:700;letter-spacing:.14em;
  padding:9px 0 11px;cursor:pointer;display:flex;flex-direction:column;
  align-items:center;gap:4px}
.navin button.on{color:var(--jade);border-top-color:var(--jade)}
.navin svg{width:19px;height:19px;stroke:currentColor;fill:none;
  stroke-width:1.6;stroke-linecap:square}
section.view{display:none} section.view.on{display:block}
.empty{color:var(--muted);font-size:12px;padding:14px 0 4px}
/* lock overlay */
#lock{position:fixed;inset:0;background:var(--ink);z-index:50;display:none;
  align-items:center;justify-content:center;padding:20px}
#lock .box{max-width:340px;width:100%}
#lock input{width:100%;background:var(--panel);border:1px solid var(--hair);
  color:var(--text);font-family:var(--mono);padding:12px;border-radius:0;
  margin:12px 0;font-size:14px}
#lock button{width:100%;background:var(--amber);color:#0B1210;border:none;
  padding:12px;font-family:var(--mono);font-weight:700;letter-spacing:.1em;
  border-radius:0;cursor:pointer}
#toast{position:fixed;left:50%;transform:translateX(-50%);bottom:86px;
  background:var(--panel2);border:1px solid var(--hair);color:var(--text);
  padding:9px 14px;border-radius:0;font-size:12px;display:none;z-index:40}
/* score state badge */
#sc-state{background:var(--amber);color:#0B1210;padding:3px 8px;
  font-size:10px;letter-spacing:.1em}
/* tier stat cards (History) */
.tiercards{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.tcard{background:var(--panel2);border:1px solid var(--hair);padding:10px 9px}
.tcard.amber{border-color:var(--amber)}
.tcard.jade{border-color:var(--jade)}
.tcard.violet{border-color:var(--violet)}
.tcard .tl{font-size:9px;font-weight:700;letter-spacing:.1em;color:var(--muted)}
.tcard.amber .tl{color:var(--amber)} .tcard.jade .tl{color:var(--jade)}
.tcard.violet .tl{color:var(--violet)}
.tcard .tn{font-size:24px;font-weight:700;letter-spacing:-.02em;margin:3px 0 1px}
.tcard .tn small{font-size:11px;color:var(--muted);font-weight:400}
.tcard .ts{font-size:10px;color:var(--muted);line-height:1.5}
/* history row figures */
.figs{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:8px}
.figs .fl{font-size:9px;font-weight:700;letter-spacing:.1em;color:var(--muted)}
.figs .fv{font-size:13px;margin-top:1px}
</style>
</head>
<body>
<header><div class="hd">
  <div class="brand"><span class="sq"></span>CRASH&nbsp;WATCH</div>
  <div class="pill"><span class="dot" id="fdot"></span><span id="flabel">STARTING</span><span class="clk" id="fclock"></span></div>
</div></header>

<div class="wrap">

<!-- ================= MONITOR ================= -->
<section class="view on" id="v-monitor">
  <div class="card">
    <div class="row"><span class="eyebrow" id="h-sym">CRASH 1000 · DERIV</span><span class="eyebrow">SYNTHETIC</span></div>
    <div class="row" style="align-items:flex-end">
      <div class="price" id="h-price">—</div>
      <div class="chg" id="h-chg"></div>
    </div>
    <div class="chips">
      <span class="chip" id="h-trend">—</span>
      <span class="chip" id="h-phase">—</span>
    </div>
    <div class="foot" id="h-foot">Waiting for the bot…</div>
  </div>

  <div class="card">
    <div class="row"><span class="eyebrow" id="c-title">LAST 40 PERIODS</span><span class="zbtns"><span class="eyebrow" id="c-range"></span><button id="z-out" aria-label="Zoom out">−</button><button id="z-in" aria-label="Zoom in">+</button></span></div>
    <div class="chartbox">
      <canvas id="chart" width="800" height="340"></canvas>
      <div class="liqtag" id="liqtag" style="display:none"></div>
    </div>
    <div class="strip">
      <span>RSI(14) <b id="s-rsi">—</b></span>
      <span>EMA(20) <b id="s-ema">—</b></span>
      <span>ATR(14) <b id="s-atr">—</b></span>
    </div>
  </div>

  <div class="card">
    <div class="row">
      <div><div class="eyebrow">CRT SCORE</div>
      <div class="serif" style="font-size:21px;margin-top:2px">Exhaustion Score</div></div>
      <div class="big"><span id="sc-v">–</span> <span>/ 5</span></div>
    </div>
    <div id="sc-rows" style="margin-top:8px"></div>
    <div class="gauge"><i id="sc-bar" style="width:0%"></i></div>
    <div class="row" style="margin-top:8px">
      <span class="mut" style="font-size:11px" id="sc-cap">Signal fires at 4 of 5.</span>
      <span class="eyebrow" id="sc-state"></span>
    </div>
  </div>

  <div id="sig-slot"></div>

  <div class="card">
    <div class="row"><span class="eyebrow">SIGNAL HISTORY (LAST 4)</span><span class="eyebrow" id="m-win"></span></div>
    <div id="m-hist"></div>
    <div class="foot" id="m-foot"></div>
  </div>
</section>

<!-- ================= HISTORY ================= -->
<section class="view" id="v-history">
  <div style="margin-top:16px">
    <div class="eyebrow">ARCHIVE · AUDIT LOG · SINCE BOT START</div>
    <div class="serif" style="font-size:27px;margin-top:3px">Signal History</div>
  </div>
  <div class="card">
    <div class="tiercards">
      <div class="tcard amber"><div class="tl">CRT SETUPS</div><div class="tn" id="tc-n">–</div><div class="ts" id="tc-s"></div></div>
      <div class="tcard jade"><div class="tl">CONFIRMED</div><div class="tn" id="tc-c-n">–</div><div class="ts" id="tc-c-s"></div></div>
      <div class="tcard violet"><div class="tl">EMA-RETEST</div><div class="tn" id="tc-e-n">–</div><div class="ts" id="tc-e-s"></div></div>
    </div>
    <div class="foot" id="st-all"></div>
    <div class="foot" id="st-foot" style="border-top:none;padding-top:0"></div>
  </div>
  <div class="fchips" id="fchips">
    <button class="fchip on" data-f="all">ALL</button>
    <button class="fchip" data-f="tp">TP HIT</button>
    <button class="fchip" data-f="sl">SL HIT</button>
    <button class="fchip" data-f="open">OPEN</button>
  </div>
  <div id="hist-list"></div>
</section>

<!-- ================= CONFIG ================= -->
<section class="view" id="v-config">
  <div style="margin-top:16px">
    <div class="eyebrow">SYSTEM · CONFIGURATION &amp; TELEMETRY</div>
    <div class="serif" style="font-size:27px;margin-top:3px">Bot Status &amp; Settings</div>
    <div class="mut" style="font-size:11px;margin-top:3px">DERIV CRASH 1000 · CRT PRE-REVERSAL ENGINE</div>
  </div>

  <div class="card">
    <div class="row"><span class="eyebrow">● CONNECTION &amp; GATEWAY</span><span class="chip" id="cf-conn">—</span></div>
    <div style="margin-top:10px">
      <div class="eyebrow" style="color:var(--faint)">ACTIVE GATEWAY ENDPOINT</div>
      <div class="note" style="margin-top:6px;word-break:break-all;color:var(--text)" id="cf-url">—</div>
    </div>
    <div id="cf-conn-rows" style="margin-top:6px"></div>
  </div>

  <div class="card">
    <div class="row"><span class="eyebrow">CRT STRATEGY PARAMETERS</span><span class="chip">READ-ONLY</span></div>
    <div id="cf-strat" style="margin-top:6px"></div>
    <div class="note">Parameters are set in the bot's config on the server. Read-only on this page — nothing here can be changed from the dashboard.</div>
  </div>

  <div class="card">
    <div class="row"><span class="eyebrow">● DELIVERY &amp; ALERT CHANNELS</span><span class="chip" id="cf-del-ready">—</span></div>
    <div id="cf-del" style="margin-top:6px"></div>
    <button class="btn" id="btn-test">SEND TEST ALERT<small>Dispatches a test message to Telegram</small></button>
    <div class="mut" style="font-size:10px;margin-top:8px">Use it to confirm delivery without waiting for a real signal.</div>
  </div>

  <div class="card">
    <div class="row"><span class="eyebrow">AI SIGNAL REASONING POLICY</span></div>
    <div class="sans" style="font-size:13px;line-height:1.55;margin-top:8px">AI explains signals only, never decides. The CRT engine triggers alerts deterministically; the AI read is display-only context posted after the signal, for human discretion.</div>
  </div>
</section>

</div><!-- /wrap -->

<nav><div class="navin">
  <button data-v="monitor" class="on"><svg viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 4 12 2.5-6h5"/></svg>MONITOR</button>
  <button data-v="history"><svg viewBox="0 0 24 24"><path d="M5 4h14M5 9h14M5 14h9M5 19h9"/></svg>HISTORY</button>
  <button data-v="config"><svg viewBox="0 0 24 24"><path d="M5 7h9M18 7h1M5 12h3M12 12h7M5 17h11M20 17h-1"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="17" r="2"/></svg>CONFIG</button>
</div></nav>

<div id="lock"><div class="box">
  <div class="brand" style="margin-bottom:6px"><span class="sq"></span>CRASH&nbsp;WATCH</div>
  <div class="mut" style="font-size:12px">This dashboard is locked. Enter the dashboard key from the link you were given.</div>
  <input id="lock-inp" type="password" placeholder="Dashboard key" autocomplete="off">
  <button id="lock-btn">UNLOCK</button>
</div></div>

<div id="toast"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let KEY = new URLSearchParams(location.search).get("k")
  || localStorage.getItem("cw_key") || "";
if (KEY) localStorage.setItem("cw_key", KEY);
let S = null, HFILTER = "all";
let CMAP = new Map(), HIST_LOADED = false, VIEW_EPOCH = null;
/* chart zoom: visible bars, 10–160. Survives data refreshes; the
   window stays anchored on VIEW_EPOCH when panned, else on live. */
let ZOOM_BARS = 40;
const ZOOM_MIN = 10, ZOOM_MAX = 160;
function zoomBy(f){ const nz=Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(ZOOM_BARS*f)));
  if(nz!==ZOOM_BARS){ ZOOM_BARS=nz; drawChart(); } }

const eatFmt = new Intl.DateTimeFormat("en-GB",{timeZone:"Africa/Nairobi",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
const eatDay = new Intl.DateTimeFormat("en-GB",{timeZone:"Africa/Nairobi",day:"2-digit",month:"short",year:"numeric"});
function fmt(n,d){ if(n===null||n===undefined||isNaN(n)) return "—";
  return Number(n).toLocaleString("en-US",{minimumFractionDigits:(d===undefined?2:d),maximumFractionDigits:(d===undefined?2:d)}); }
function sgn(n,d){ if(n===null||n===undefined) return "—"; const v=Number(n);
  return (v>0?"+":v<0?"-":"") + fmt(Math.abs(v),d); }
function dur(sec){ if(sec===null||sec===undefined) return "—"; sec=Math.round(sec);
  const d=Math.floor(sec/86400), h=Math.floor(sec%86400/3600), m=Math.floor(sec%3600/60), s=sec%60;
  if(d) return d+"d "+String(h).padStart(2,"0")+":"+String(m).padStart(2,"0")+":"+String(s).padStart(2,"0");
  if(h) return h+"h "+String(m).padStart(2,"0")+"m";
  if(m) return m+"m "+String(s).padStart(2,"s")+"s";
  return s+"s"; }
function ago(sec){ if(sec===null||sec===undefined) return "no ping recorded yet";
  if(sec<90) return "just now"; if(sec<3600) return Math.round(sec/60)+"m ago";
  return Math.round(sec/3600)+"h ago"; }
function esc(t){ return String(t===null||t===undefined?"":t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }

async function load(){
  try{
    const r = await fetch("/status.json",{headers:{"X-Dash-Key":KEY},cache:"no-store"});
    if(r.status===401){ $("lock").style.display="flex"; return; }
    if(!r.ok) throw new Error("bad");
    S = await r.json();
    if(!HIST_LOADED){
      try{
        const hr=await fetch("/candles",{headers:{"X-Dash-Key":KEY},cache:"no-store"});
        if(hr.ok){ const hj=await hr.json();
          (hj.candles||[]).forEach(c=>CMAP.set(c.t,c)); HIST_LOADED=true; }
      }catch(e){ /* retry on the next load */ }
    }
    $("lock").style.display="none";
    render();
  }catch(e){
    $("fdot").className="dot down"; $("flabel").textContent="BOT UNREACHABLE";
  }
}
$("lock-btn").onclick = ()=>{ KEY=$("lock-inp").value.trim();
  localStorage.setItem("cw_key",KEY); load(); };

/* ---------- nav ---------- */
document.querySelectorAll("nav button").forEach(b=>{
  b.onclick=()=>{ document.querySelectorAll("nav button").forEach(x=>x.classList.remove("on"));
    b.classList.add("on");
    document.querySelectorAll("section.view").forEach(x=>x.classList.remove("on"));
    $("v-"+b.dataset.v).classList.add("on"); window.scrollTo(0,0); };
});
function goHistory(){ document.querySelector('nav button[data-v="history"]').click(); }

/* ---------- shared bits ---------- */
function outcomeHTML(r){
  if(r.outcome==="tp") return '<span class="outcome grn">TP HIT '+sgn(r.points,1)+' PTS</span>';
  if(r.outcome==="sl") return '<span class="outcome red">SL HIT '+sgn(r.points,1)+' PTS</span>';
  return '<span class="outcome mut">OPEN — TRACKING</span>';
}
function tagHTML(side){ return '<span class="tag'+(side==="BUY"?" buy":"")+'">'+side+'</span>'; }
function tierHTML(r){
  if(r.tier==="confirmed") return '<span class="tag" style="border-color:var(--green);color:var(--green)">CONF</span>';
  if(r.tier==="ema_retest") return '<span class="tag" style="border-color:var(--violet);color:var(--violet)">EMA</span>';
  if(r.confirmed) return '<span style="font-size:10px" class="grn">✓ CONF</span>';
  return '';
}
function condMini(r){
  const names={sweep:"CRT sweep",consecutive:"Drift candles",rsi:"RSI extreme",stretch:"Stretch from EMA",move:"Move size"};
  return Object.keys(names).map(k=>{
    const met=r.conditions && r.conditions[k];
    return '<div><span class="'+(met?"grn":"mut")+'" style="width:14px">'+(met?"✓":"—")+'</span><span>'+names[k]+'</span></div>';
  }).join("");
}
function histRow(r, expandable){
  const fig=(l,v,cls)=> '<div><div class="fl">'+l+'</div><div class="fv '+(cls||"")+'">'+v+'</div></div>';
  let h='<div class="hrow" data-oc="'+r.outcome+'">'
    +'<div class="hmain"><span class="tm">'+r.time_eat+'</span>'+tagHTML(r.side)
    +(r.score===null||r.score===undefined?'':'<span style="font-size:11px" class="mut">'+r.score+'/5</span>')
    + tierHTML(r)
    + outcomeHTML(r) +'</div>'
    +'<div class="figs">'
    + fig("ENTRY", fmt(r.entry))
    + fig("SL", fmt(r.sl), "red")
    + fig("TP", fmt(r.tp), "grn")
    + fig("R:R", r.rr?("1 : "+fmt(r.rr,1)):"—")
    +'</div>'
    +(r.explanation?'<div class="foot mut">AI READ · '+esc(r.explanation)+'</div>':'');
  if(expandable){
    h+='<div class="hdetail"><div class="sltp">'
      +(r.setup_time_eat? '<span>SETUP <b>'+r.setup_time_eat+'</b></span>' : '')
      +'<span>TIME <b>'+dur(r.duration_sec)+'</b></span></div>'
      +(r.conditions?'<div class="condmini">'+condMini(r)+'</div>':'')+'</div>';
  }
  return h+'</div>';
}

/* ---------- render ---------- */
function render(){
  /* header */
  const live=S.connected;
  const reconn= !live && S.system.last_drop_epoch && (S.now - S.system.last_drop_epoch) < 30;
  $("fdot").className="dot"+(live?" live":" down");
  $("fdot").style.background= reconn? "var(--rust)" : "";
  $("flabel").textContent= live? "FEED LIVE" : (reconn? "RECONNECTING" : "FEED DOWN");
  $("fclock").textContent="· "+eatFmt.format(new Date())+" EAT";
  /* hero */
  $("h-sym").textContent=(S.symbol==="CRASH1000"?"CRASH 1000":S.symbol)+" · DERIV · "+S.config.timeframe;
  $("h-price").textContent=fmt(S.price);
  if(S.change_1h){ const c=S.change_1h, up=c.abs>=0;
    $("h-chg").innerHTML='<div class="'+(up?"grn":"red")+'">'+(up?"▲ ":"▼ ")+sgn(c.abs)+' 1H</div>'
      +'<div class="mut">'+sgn(c.pct,2)+'% · LAST HOUR</div>';
  } else $("h-chg").innerHTML="";
  $("h-trend").textContent=(S.trend||"—").toUpperCase()+(S.trend==="uptrend"?" — DRIFT":"");
  $("h-phase").textContent="PHASE: "+((S.phase||"—").toUpperCase())+(S.phase_direction?" · "+S.phase_direction.toUpperCase():"");
  $("h-foot").textContent="Endpoint: "+(S.endpoint_label||"—")+" · Candles: "+fmt(S.candles_closed,0)+" · Updated "+(S.now_eat||"").slice(11)+" EAT";
  $("s-rsi").textContent=fmt(S.rsi,1);
  $("s-ema").textContent=fmt(S.ema);
  $("s-atr").textContent=fmt(S.atr);
  drawChart();
  /* score */
  const sc=S.score;
  $("sc-v").textContent=sc.value;
  $("sc-bar").style.width=(sc.value/5*100)+"%";
  $("sc-cap").textContent="Signal fires at "+sc.min+" of 5.";
  $("sc-state").textContent= sc.value>=sc.min? "AT THRESHOLD" : "ARMED — WATCHING";
  $("sc-rows").innerHTML=sc.conditions.map(c=>
    '<div class="cond"><span class="mark '+(c.met?"grn":"mut")+'">'+(c.met?"✓":"—")+'</span>'
    +'<span class="lab">'+c.label+'</span>'
    +'<span class="val"><b>'+(c.value==="met"||c.value==="waiting"?"":esc(c.value))+'</b></span>'
    +'<span style="font-size:10px;font-weight:700;letter-spacing:.08em" class="'+(c.met?"grn":"mut")+'">'+(c.met?"MET":"PENDING")+'</span></div>').join("");
  /* last signal */
  const ls=S.last_signal, slot=$("sig-slot");
  if(ls){
    const tierCol= ls.tier==="confirmed"? "var(--jade)" : ls.tier==="ema_retest"? "var(--violet)" : "var(--amber)";
    slot.innerHTML='<div class="card sig'+(ls.side==="BUY"?" buy":"")+'" style="border-left-color:'+tierCol+'">'
      +'<div class="row"><span class="eyebrow" style="color:'+tierCol+'">LAST SIGNAL'+(ls.tier==="confirmed"?" · CONFIRMED TIER":(ls.tier==="ema_retest"?" · EMA-RETEST SHADOW":""))+'</span>'+outcomeHTML(ls)+'</div>'
      +'<div class="sigtitle">'+(ls.tier==="confirmed"?"CONFIRMED PRE-REVERSAL ":(ls.tier==="ema_retest"?"EMA-RETEST SHADOW ":"PRE-REVERSAL "))+ls.side+' — '+ls.time_eat+' EAT</div>'
      +'<div class="figures">Entry '+fmt(ls.entry)+' &nbsp;·&nbsp; SL '+fmt(ls.sl)+' &nbsp;·&nbsp; TP '+fmt(ls.tp)+'</div>'
      +(ls.explanation?'<div class="foot mut">AI READ · '+esc(ls.explanation)+'</div>':'')
      +'<div class="foot">'+(ls.tier==="ema_retest"?"Shadow tier — backtest +2.80 expectancy on 24 trades (not proven)":("Score "+ls.score+"/5"))+(ls.duration_sec!==null&&ls.duration_sec!==undefined?(' · closed in '+dur(ls.duration_sec)):' · still open')+'</div></div>';
  } else {
    slot.innerHTML='<div class="card"><span class="eyebrow">LAST SIGNAL</span>'
      +'<div class="empty">No signal fired yet since the bot started. When the score reaches '+S.score.min+' of 5, the signal appears here and on Telegram at the same moment.</div></div>';
  }
  /* monitor history preview */
  $("m-win").textContent= S.stats.hit_rate!==null? ("WIN RATE "+S.stats.hit_rate+"%") : "";
  $("m-hist").innerHTML= S.history.length? S.history.slice(0,4).map(r=>histRow(r,false)).join("")
    : '<div class="empty">No signals yet.</div>';
  $("m-foot").textContent="Host: Render · Uptime "+dur(S.system.uptime_sec)+" · Heartbeat "+(S.now_eat||"").slice(11)+" EAT";
  /* history view — tier cards, all figures from the snapshot stats */
  const tcard=(elN, elS, t, extra)=>{
    $(elN).textContent=t.signals;
    $(elS).innerHTML="hit "+(t.hit_rate!==null? t.hit_rate+"%" : "—")
      +" · "+t.tp+" TP / "+t.sl+" SL · "+sgn(t.total_points,1)+" pts"
      +(t.open? " · "+t.open+" open" : "")+(extra||"");
  };
  tcard("tc-n","tc-s", S.stats,
    S.stats.avg_rr!==null? " · avg 1:"+fmt(S.stats.avg_rr,1) : "");
  const cs=S.stats.confirmed;
  tcard("tc-c-n","tc-c-s", cs,
    " · confirms within "+S.config.confirm_window+" bars");
  const em=S.stats.ema_retest;
  const emPaused = S.config.ema_retest && S.config.ema_retest.enabled===false;
  tcard("tc-e-n","tc-e-s", em, emPaused? " · PAUSED" : " · JUDGING");
  const allT=[S.stats, cs, em];
  const totTp=allT.reduce((a,t)=>a+t.tp,0), totSl=allT.reduce((a,t)=>a+t.sl,0);
  const totPts=allT.reduce((a,t)=>a+t.total_points,0);
  const totSig=allT.reduce((a,t)=>a+t.signals,0);
  $("st-all").textContent="ALL TIERS · "+totSig+" signals · hit "
    +(totTp+totSl? Math.round(totTp/(totTp+totSl)*1000)/10+"%" : "—")
    +" · "+sgn(totPts,1)+" pts";
  $("st-foot").textContent="Deriv "+S.symbol+" · CRT Pre-Reversal · Sync "+(S.now_eat||"")+" EAT";
  renderHistory();
  /* config view */
  $("cf-conn").textContent= live? "CONNECTED" : (reconn? "RECONNECTING" : "DOWN");
  $("cf-url").textContent= S.endpoint_url || "— (not connected yet)";
  const kv=(k,v)=> '<div class="kv"><span class="k">'+k+'</span><span class="v">'+v+'</span></div>';
  $("cf-conn-rows").innerHTML =
    kv("Instrument", S.symbol+" (Synthetic)")
    + kv("Candle Timeframe", S.config.timeframe+" ("+tfSec()+"s)")
    + kv("Buffer Fill Level", fmt(S.candles_closed,0)+" / "+fmt(S.buffer_keep,0))
    + kv("Last Feed Connected", S.system.last_connect_eat||"—")
    + kv("Last Feed Drop", S.system.last_drop_eat||"—")
    + kv("Process Uptime", dur(S.system.uptime_sec))
    + kv("Host Runtime", S.system.host+" (free)");
  const c=S.config;
  $("cf-strat").innerHTML =
    kv("Execution Timeframe", c.timeframe)
    + kv("Min Exhaustion Score", c.min_score+" of 5")
    + kv("RSI Period / Bounds", "RSI("+c.rsi_period+") · sell ≥ "+(100-c.rsi_level)+" · buy ≤ "+c.rsi_level)
    + kv("Stretch Threshold", fmt(c.stretch_atr,1)+" ATR (EMA "+c.stretch_ema+")")
    + kv("Minimum Move Size", fmt(c.drop_atr,1)+" ATR over "+c.drop_lookback+" bars")
    + kv("Stop Loss (SL)", fmt(c.sl_atr,1)+" ATR beyond the extreme")
    + kv("Take Profit (TP)", c.tp_mode==="avg_spike"? (fmt(c.tp_frac,1)+" × avg spike (fallback "+fmt(c.rr,1)+"R)") : (fmt(c.rr,1)+"R"))
    + kv("Drift Pattern", c.min_consecutive+" consecutive candles")
    + kv("Sweep Range", "prev "+c.range_bars+" bars")
    + kv("Confirmation Tier", "displacement within "+c.confirm_window+" bars of the setup");
  const emc=c.ema_retest;
  if(emc){
    $("cf-strat").innerHTML +=
      kv("EMA-Retest Tier (Shadow)", emc.trigger+" · "+emc.trend)
      + kv("Shadow Status", emc.enabled===false? '<span style="color:var(--red)">● PAUSED</span>' : '<span class="grn">● ACTIVE</span>')
      + kv("Shadow Stop / Target", emc.sl+" · "+emc.tp)
      + kv("Shadow Judgment", emc.judgment);
  }
  $("cf-del-ready").textContent= S.delivery.telegram? "READY" : "NOT SET";
  $("cf-del").innerHTML =
    kv("Telegram Bot", S.delivery.telegram? '<span class="grn">● ACTIVE</span>' : "not configured")
    + kv("Target Destination", "Private chat (hidden)")
    + kv("Health Endpoint", "200 OK (/health)")
    + kv("Pinger / Keepalive", ago(S.system.last_ping_ago_sec))
    + kv("Dashboard Access", KEY? "Key-locked link" : "Open (no key set)");
}

function renderHistory(){
  const list=$("hist-list");
  const rows=S.history.filter(r=> HFILTER==="all" || r.outcome===HFILTER);
  if(!rows.length){ list.innerHTML='<div class="empty">No signals in this view yet.</div>'; return; }
  const todayE = new Intl.DateTimeFormat("en-CA",{timeZone:"Africa/Nairobi",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
  let html="", cur="";
  rows.forEach(r=>{
    if(r.date_eat!==cur){ cur=r.date_eat;
      const d=new Date(r.epoch*1000);
      const lbl=(cur===todayE? "TODAY — " : "")+eatDay.format(d).toUpperCase();
      const cnt=S.history.filter(x=>x.date_eat===cur).length;
      html+='<div class="daylab"><span class="eyebrow">'+lbl+'</span><span class="eyebrow">'+cnt+' EVENTS</span></div>';
    }
    html+=histRow(r,true);
  });
  list.innerHTML=html;
  list.querySelectorAll(".hrow").forEach(el=>{ el.onclick=()=>el.classList.toggle("open"); });
}
document.querySelectorAll("#fchips .fchip").forEach(b=>{
  b.onclick=()=>{ document.querySelectorAll("#fchips .fchip").forEach(x=>x.classList.remove("on"));
    b.classList.add("on"); HFILTER=b.dataset.f; if(S) renderHistory(); };
});
$("m-hist").addEventListener("click", goHistory);

/* ---------- chart ---------- */
function tfSec(){ const m=/^M(\d+)$/.exec((S.config&&S.config.timeframe)||"");
  return m? parseInt(m[1])*60 : 60; }
function allCandles(){
  (S.candles||[]).forEach(c=>CMAP.set(c.t,c));
  if(CMAP.size>2000){ const ks=[...CMAP.keys()].sort((a,b)=>a-b);
    ks.slice(0, CMAP.size-2000).forEach(k=>CMAP.delete(k)); }
  return [...CMAP.values()].sort((a,b)=>a.t-b.t);
}
function drawChart(){
  const cv=$("chart"); if(!cv||!S) return;
  const ctx=cv.getContext("2d"), W=cv.width, H=cv.height, P=8;
  ctx.clearRect(0,0,W,H);
  const all=allCandles();
  let endIdx=all.length;
  if(VIEW_EPOCH!==null){ const fi=all.findIndex(c=>c.t===VIEW_EPOCH);
    endIdx = fi<0? all.length : fi+1; }
  const cs=all.slice(Math.max(0,endIdx-ZOOM_BARS), endIdx);
  if(!cs.length){ $("c-range").textContent=""; return; }
  $("c-title").textContent=(VIEW_EPOCH!==null?"SHOWING ":"LAST ")+cs.length+" "+S.config.timeframe+" PERIODS";
  const hhmm=t=>eatFmt.format(new Date(t*1000)).slice(0,5);
  $("c-range").textContent=hhmm(cs[0].t)+"–"+hhmm(cs[cs.length-1].t)
    +(VIEW_EPOCH!==null?" · DRAG LEFT FOR LIVE":"");
  /* trade levels follow the latest OPEN signal only — dynamic: once a
     trade closes (TP/SL hit) its ENTRY/TP/SL lines leave the chart
     and only its EXIT marker remains. History is newest-first, so
     the first open record is the latest live trade. */
  const ls=S.last_signal;
  const openSig=(S.history||[]).find(r=>r.outcome==="open") || null;
  let lv=null;
  if(openSig) lv={entry:openSig.entry,
             tp1:openSig.entry+(openSig.tp-openSig.entry)/2,
             tp2:openSig.tp, sl:openSig.sl};
  let lo=Infinity, hi=-Infinity;
  cs.forEach(c=>{ lo=Math.min(lo,c.l); hi=Math.max(hi,c.h); });
  if(S.liquidity && S.liquidity>lo && S.liquidity<hi*1.02){ hi=Math.max(hi,S.liquidity); }
  if(lv) [lv.entry,lv.tp1,lv.tp2,lv.sl].forEach(v=>{ lo=Math.min(lo,v); hi=Math.max(hi,v); });
  if(ls && (ls.outcome==="tp"||ls.outcome==="sl"))
    [ls.tp,ls.sl].forEach(v=>{ lo=Math.min(lo,v); hi=Math.max(hi,v); });
  const pad=(hi-lo)*0.08||1; hi+=pad; lo-=pad;
  const y=v=> H-P-((v-lo)/(hi-lo))*(H-2*P);
  ctx.strokeStyle="#1C332B"; ctx.lineWidth=1;
  [0.33,0.66].forEach(f=>{ const yy=P+f*(H-2*P);
    ctx.beginPath(); ctx.moveTo(0,yy); ctx.lineTo(W,yy); ctx.stroke(); });
  const step=W/cs.length, bw=Math.max(3,step*0.55);
  cs.forEach((c,i)=>{ const x=P+i*step+step/2, up=c.c>=c.o;
    ctx.strokeStyle= up? "#2FCE8F":"#FF6A5E"; ctx.fillStyle=ctx.strokeStyle;
    ctx.beginPath(); ctx.moveTo(x,y(c.h)); ctx.lineTo(x,y(c.l)); ctx.stroke();
    const t=y(Math.max(c.o,c.c)), b=y(Math.min(c.o,c.c));
    ctx.fillRect(x-bw/2, t, bw, Math.max(2,b-t)); });
  const tag=$("liqtag");
  if(S.liquidity && S.liquidity>lo && S.liquidity<hi){
    const yy=y(S.liquidity);
    ctx.strokeStyle="#9B8CFF"; ctx.setLineDash([6,5]);
    ctx.beginPath(); ctx.moveTo(0,yy); ctx.lineTo(W,yy); ctx.stroke();
    ctx.setLineDash([]);
    /* nudge the tag below the line when a level label sits on it,
       so the tag never covers an ENTRY/TP/SL label */
    let off=0;
    if(lv) [lv.entry,lv.tp1,lv.tp2,lv.sl].forEach(v=>{
      if(Math.abs(y(v)-yy)<28) off=30; });
    tag.style.display="block"; tag.style.top=((yy+off)/H*100)+"%";
    tag.textContent="liquidity "+fmt(S.liquidity);
  } else tag.style.display="none";
  const xOf=ep=>{ const i=cs.findIndex(c=>c.t===ep);
    return i<0? null : P+i*step+step/2; };
  if(lv){
    const hline=(v,color,dash,label)=>{
      const yy=y(v); if(yy<P-4||yy>H-P+4) return;
      ctx.strokeStyle=color; ctx.lineWidth=2; ctx.setLineDash(dash||[]);
      ctx.beginPath(); ctx.moveTo(0,yy); ctx.lineTo(W,yy); ctx.stroke();
      ctx.setLineDash([]); ctx.lineWidth=1;
      ctx.font="700 19px 'Space Mono',monospace";
      const tw=ctx.measureText(label).width;
      ctx.fillStyle="#0B1210"; ctx.fillRect(W-tw-14, yy-12, tw+10, 24);
      ctx.fillStyle=color; ctx.fillText(label, W-tw-9, yy+7);
    };
    hline(lv.sl, "#FF6A5E", [10,6], "SL "+fmt(lv.sl));
    hline(lv.tp2, "#2FCE8F", [], "TP2 "+fmt(lv.tp2));
    hline(lv.tp1, "#2FCE8F", [10,6], "TP1 "+fmt(lv.tp1));
    hline(lv.entry, "#F2F7F5", [], "ENTRY "+fmt(lv.entry));
    /* entry marker: faint vertical + dot on the signal bar */
    const ex=xOf(openSig.epoch);
    if(ex!==null){
      ctx.strokeStyle="rgba(242,247,245,.35)"; ctx.setLineDash([3,6]);
      ctx.beginPath(); ctx.moveTo(ex,P); ctx.lineTo(ex,H-P); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle="#F2F7F5";
      ctx.beginPath(); ctx.arc(ex, y(openSig.entry), 5, 0, 7); ctx.fill();
    }
    /* entry dots for any other OPEN signals inside the window */
    (S.history||[]).forEach(r=>{
      if(r.outcome!=="open") return;
      if(r.epoch===openSig.epoch) return;
      const x=xOf(r.epoch); if(x===null) return;
      ctx.fillStyle="#638578";
      ctx.beginPath(); ctx.arc(x, y(r.entry), 4, 0, 7); ctx.fill();
    });
  }
  /* exit marker where the latest signal closed (TP or SL) */
  if(ls && (ls.outcome==="tp"||ls.outcome==="sl") && ls.closed_epoch){
    const fx=(ls.closed_epoch-cs[0].t)/tfSec();
    if(fx>=0 && fx<cs.length){
      const x=P+fx*step+step/2, yy=y(ls.outcome==="tp"? ls.tp : ls.sl);
      const col= ls.outcome==="tp"? "#2FCE8F":"#FF6A5E";
      ctx.strokeStyle=col; ctx.lineWidth=3;
      const r=8;
      ctx.beginPath();
      ctx.moveTo(x-r,yy-r); ctx.lineTo(x+r,yy+r);
      ctx.moveTo(x+r,yy-r); ctx.lineTo(x-r,yy+r);
      ctx.stroke(); ctx.lineWidth=1;
      ctx.font="700 19px 'Space Mono',monospace";
      const label="EXIT "+sgn(ls.points,1);
      ctx.fillStyle=col;
      ctx.fillText(label, Math.min(x+12, W-ctx.measureText(label).width-6), yy-12);
    }
  }
}

/* ---------- chart pan + zoom: drag to look back, pinch/wheel/buttons to zoom ---------- */
(function(){
  const cv=$("chart"); if(!cv) return;
  let dragging=false, startX=0, startIdx=0;
  const ptrs=new Map();
  let pinch=null; /* {dist, zoom} while two pointers are down */
  const toCanvasX=e=>{ const r=cv.getBoundingClientRect();
    return (e.clientX-r.left)*(cv.width/r.width); };
  const curIdx=()=>{ const all=allCandles();
    if(VIEW_EPOCH===null) return all.length-1;
    const fi=all.findIndex(c=>c.t===VIEW_EPOCH);
    return fi<0? all.length-1 : fi; };
  cv.addEventListener("pointerdown", e=>{
    const all=allCandles(); if(all.length<2) return;
    ptrs.set(e.pointerId, toCanvasX(e));
    try{ cv.setPointerCapture(e.pointerId); }catch(err){}
    if(ptrs.size===2){
      dragging=false;
      const xs=[...ptrs.values()];
      pinch={dist:Math.abs(xs[0]-xs[1])||1, zoom:ZOOM_BARS};
      return;
    }
    dragging=true; startX=toCanvasX(e); startIdx=curIdx();
  });
  cv.addEventListener("pointermove", e=>{
    if(!ptrs.has(e.pointerId)) return;
    ptrs.set(e.pointerId, toCanvasX(e));
    if(pinch && ptrs.size>=2){
      const xs=[...ptrs.values()];
      const d=Math.abs(xs[0]-xs[1])||1;
      const nz=Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(pinch.zoom*pinch.dist/d)));
      if(nz!==ZOOM_BARS){ ZOOM_BARS=nz; drawChart(); }
      return;
    }
    if(!dragging) return;
    const all=allCandles(); if(all.length<2) return;
    const dx=toCanvasX(e)-startX;
    const lo=Math.min(ZOOM_BARS-1, all.length-1), hi=all.length-1;
    const idx=Math.max(lo, Math.min(hi, startIdx-Math.round(dx/(cv.width/ZOOM_BARS))));
    VIEW_EPOCH = idx>=hi? null : all[idx].t;
    drawChart();
  });
  const end=e=>{
    ptrs.delete(e.pointerId);
    if(ptrs.size<2) pinch=null;
    if(ptrs.size===1){
      /* pinch ended with one finger still down: resume the drag
         from here so the chart does not jump */
      dragging=true; startX=[...ptrs.values()][0]; startIdx=curIdx();
    } else dragging=false;
  };
  cv.addEventListener("pointerup", end);
  cv.addEventListener("pointercancel", end);
  cv.addEventListener("dblclick", ()=>{ VIEW_EPOCH=null; drawChart(); });
  cv.addEventListener("wheel", e=>{
    e.preventDefault();
    zoomBy(e.deltaY>0?1.25:0.8);
  }, {passive:false});
})();

$("z-in").onclick=()=>zoomBy(0.8);
$("z-out").onclick=()=>zoomBy(1.25);

/* ---------- test alert ---------- */
$("btn-test").onclick=async()=>{
  const b=$("btn-test"); b.disabled=true;
  try{
    const r=await fetch("/test-alert",{method:"POST",headers:{"X-Dash-Key":KEY}});
    const j=await r.json();
    toast(j.sent? "Test alert sent — check Telegram."
      : "Send failed: "+(j.detail || "unknown error")+" — tell Ziggy this message.");
  }catch(e){ toast("Could not reach the bot."); }
  b.disabled=false;
};
function toast(t){ const el=$("toast"); el.textContent=t; el.style.display="block";
  setTimeout(()=>el.style.display="none", 3600); }

load();
setInterval(load, 5000);
setInterval(()=>{ $("fclock").textContent="· "+eatFmt.format(new Date())+" EAT"; }, 1000);
</script>
</body>
</html>
"""
