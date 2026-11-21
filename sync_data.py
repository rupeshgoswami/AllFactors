"""Share the fetched database through GitHub, without bloating the repo history.

  python sync_data.py publish   gzip data/portal.db and force-push it as ONE commit to
                                branch 'data-snapshot' (old snapshots are replaced, so the
                                repo never grows by more than one copy)
  python sync_data.py pull      download that snapshot into data/portal.db
                                (your current DB is kept as data/portal.before-pull.db)
"""
import gzip, os, shutil, sqlite3, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "portal.db"
BRANCH = "data-snapshot"
NAME = "portal.db.gz"


def git(*args, cwd=ROOT, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if check and r.returncode:
        sys.exit("git %s failed:\n%s" % (" ".join(args), r.stderr.decode(errors="replace")))
    return r


def publish():
    if not DB.exists():
        sys.exit("No data/portal.db yet. Run ingest.py first.")
    con = sqlite3.connect(DB)
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("Database failed integrity check; not publishing.")
    con.close()
    url = git("remote", "get-url", "origin").stdout.decode().strip()
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "snap.db"
        src = sqlite3.connect(DB); dst = sqlite3.connect(snap)
        src.backup(dst); dst.close(); src.close()          # consistent copy
        with open(snap, "rb") as f, gzip.open(Path(tmp) / NAME, "wb", 9) as g:
            shutil.copyfileobj(f, g)
        snap.unlink()
        size = (Path(tmp) / NAME).stat().st_size / 1e6
        (Path(tmp) / "UPDATED.txt").write_text(datetime.now().isoformat(timespec="seconds"))
        git("init", "-q", "-b", BRANCH, cwd=tmp)
        git("add", ".", cwd=tmp)
        git("-c", "user.name=portal", "-c", "user.email=portal@local",
            "commit", "-q", "-m", "Data snapshot " + datetime.now().strftime("%Y-%m-%d %H:%M"), cwd=tmp)
        print("Uploading %.1f MB to branch %s ..." % (size, BRANCH))
        git("push", "-q", "--force", url, "%s:%s" % (BRANCH, BRANCH), cwd=tmp)
    print("Published. On another machine: python sync_data.py pull")


def pull():
    remote = "origin"
    tok = os.environ.get("GITHUB_TOKEN")      # only needed when the repo is private (hosted app)
    if tok:
        u = git("remote", "get-url", "origin").stdout.decode().strip()
        remote = u.replace("https://", "https://x-access-token:%s@" % tok, 1)
    git("fetch", "-q", "--depth", "1", remote, BRANCH)
    blob = git("show", "FETCH_HEAD:" + NAME).stdout
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        shutil.copy2(DB, DB.with_name("portal.before-pull.db"))
    tmp = DB.with_suffix(".db.new")
    with open(tmp, "wb") as f:
        f.write(gzip.decompress(blob))
    os.replace(tmp, DB)
    print("Pulled snapshot into", DB)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"publish": publish, "pull": pull}.get(cmd, lambda: sys.exit(__doc__))()
