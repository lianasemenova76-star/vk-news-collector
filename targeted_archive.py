#!/usr/bin/env python3
"""Search public VK wall text and stream matches to Google Sheets."""
import argparse,csv,json,os,re,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent
GROUP=-77822465
MSK=ZoneInfo("Europe/Moscow")
def matches(s,patterns):
    n=s.lower().replace("ё","е")
    return [(label,sorted({s[m.start():m.end()] for m in rx.finditer(n)})) for label,rx in patterns if rx.search(n)]
def get_page(token,offset):
    body=urlencode({"access_token":token,"v":"5.199","owner_id":GROUP,"count":100,"offset":offset,"filter":"owner"}).encode()
    for retry in range(6):
        req=Request("https://api.vk.com/method/wall.get",data=body,headers={"User-Agent":"targeted-text-index/1.0"})
        try:
            with urlopen(req,timeout=65) as r: payload=json.load(r)
            if "response" in payload:return payload["response"]
            error=payload.get("error",{})
            if error.get("error_code") not in (6,9,10):raise RuntimeError(str(error.get("error_code"))+": "+str(error.get("error_msg")))
        except (TimeoutError,OSError):
            if retry==5:raise
        time.sleep(2**retry)
    raise RuntimeError("Exhausted API retries")
def drive_call(payload):
    url=os.environ.get("ARCHIVE_WEBAPP_URL")
    secret=os.environ.get("ARCHIVE_WEBAPP_TOKEN")
    if not url or not secret:
        raise RuntimeError("Set ARCHIVE_WEBAPP_URL and ARCHIVE_WEBAPP_TOKEN GitHub secrets")
    request=Request(url,data=json.dumps({"token":secret,**payload},ensure_ascii=False).encode(),headers={"Content-Type":"application/json"},method="POST")
    for retry in range(5):
        try:
            with urlopen(request,timeout=90) as response:
                result=json.load(response)
            if result.get("ok"): return result
            if result.get("error")!="busy": raise RuntimeError("Drive receiver: "+str(result.get("error")))
        except (TimeoutError,OSError,ValueError) as exc:
            if retry==4: raise RuntimeError("Drive connection failed") from exc
        time.sleep(2**retry)
    raise RuntimeError("Drive connection failed")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--start",default="2021-10-17")
    ap.add_argument("--end",default="2026-10-17")
    ap.add_argument("--max-pages",type=int,default=2000)
    ap.add_argument("--rewind-pages",type=int,default=0)
    ap.add_argument("--archive-dir",default="data/archive")
    ap.add_argument("--mode",choices=["live","archive"],default="live")
    ap.add_argument("--out",default="targeted-output")
    args=ap.parse_args()
    start=int(datetime.fromisoformat(args.start+"T00:00:00+03:00").timestamp())
    end=int(datetime.fromisoformat(args.end+"T23:59:59+03:00").timestamp())
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    d=json.loads((ROOT/"targeted_keywords.json").read_text(encoding="utf-8"))
    patterns=[(x["label"],re.compile(r"(?<!\w)(?:"+x["regex"]+r")(?!\w)",re.I)) for x in d["keywords"]]
    db=sqlite3.connect(out/"matched.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY,date TEXT,text TEXT,url TEXT,terms TEXT,forms TEXT)")
    checked=set(); seen=0; matched=0; pages=0; completed=False
    def process(post):
        nonlocal seen,matched
        owner=int(post.get("owner_id",0));ts=int(post.get("date",0)); pid=post.get("id")
        if owner!=GROUP or pid is None or ts<start or ts>end or pid in checked:return
        checked.add(pid);seen+=1
        txt=post.get("text") or ""
        hits=matches(txt,patterns)
        if not hits:return
        date=datetime.fromtimestamp(ts,timezone.utc).astimezone(MSK).isoformat()
        db.execute("INSERT OR IGNORE INTO posts VALUES (?,?,?,?,?,?)",(str(owner)+"_"+str(pid),date,txt,"https://vk.ru/wall"+str(owner)+"_"+str(pid),", ".join(x[0] for x in hits),json.dumps(hits,ensure_ascii=False)))
        matched+=1
    if args.mode=="archive":
        for file in sorted(Path(args.archive_dir).glob("*.json")):
            j=json.loads(file.read_text(encoding="utf-8"));pages+=1
            for x in j.get("posts",[]):
                if x.get("source")!="tipitutaev":continue
                try:
                    owner,pid=map(int,x["id"].split("_"))
                    ts=int(datetime.fromisoformat(x["published_at"]).timestamp())
                    process({"owner_id":owner,"id":pid,"date":ts,"text":x.get("text","")})
                except (KeyError,ValueError):continue
            db.commit()
    else:
        first_offset=int(drive_call({"action":"status"})["offset"])
        offset_base=max(0,first_offset-100*args.rewind_pages)
        print(f"Starting VK API scan at offset {offset_base} (saved checkpoint: {first_offset}); period: {args.start} to {args.end}",flush=True)
        token=os.environ.get("VK_SERVICE_TOKEN")
        if not token:raise RuntimeError("Missing VK_SERVICE_TOKEN")
        for page in range(args.max_pages):
            offset=offset_base+page*100
            items=get_page(token,offset).get("items",[])
            if not items:completed=True;break
            pages+=1
            for item in items:process(item)
            db.commit()
            records=[dict(zip(("date","terms","text","url","id","forms"),r)) for r in db.execute("SELECT date,terms,text,url,id,forms FROM posts")]
            for k in range(0,len(records),100):
                drive_call({"action":"append","records":records[k:k+100],"next_offset":offset})
            drive_call({"action":"append","records":[],"next_offset":offset+len(items)})
            db.execute("DELETE FROM posts")
            db.commit()
            ordinary=[int(p["date"]) for p in items if not p.get("is_pinned")]
            if ordinary and min(ordinary)<start:completed=True;break
            if len(items)<100:completed=True;break
            if page%20==0:print("Pages",pages,"checked",seen,"matched",matched,flush=True)
            time.sleep(.45)
    if args.mode=="live":
        print(f"Scan done: {pages} pages, {seen} unique posts checked, {matched} keyword matches, reached requested start: {completed}",flush=True)
        db.close()
        return 0
    with (out/"matched.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f);w.writerow(["Дата","Найденные запросы","Полный текст","Ссылка","ID","Совпавшие формы"])
        for row in db.execute("SELECT date,terms,text,url,id,forms FROM posts ORDER BY date"):
            w.writerow([("'" + x if isinstance(x,str) and x.startswith(("=","+","-","@")) else x) for x in row])
    summary={"mode":args.mode,"group_id":GROUP,"date_start":args.start,"date_end":args.end,"pages_or_files":pages,"checked_unique":seen,"matched":matched,"stored_unique":db.execute("SELECT count(*) FROM posts").fetchone()[0],"reached_start_or_end":completed}
    (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
    db.close()
if __name__=="__main__":main()
