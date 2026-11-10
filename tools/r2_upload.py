#!/usr/bin/env python
"""Upload a local folder to the Cloudflare R2 bucket, then VERIFY it.

    python tools/r2_upload.py <local_dir> <r2_key_prefix> [--always <file name>]...

e.g. python tools/r2_upload.py \
        "site/clients/Conterra/Partial Slab Replacement/pointclouds/scan" \
        conterra/partialslab/pointclouds/scan

Why this exists: an interrupted R2 sync LOOKS finished. The 410john upload
stopped 30 objects short with no error while cloud.js / sources.json /
stations.json were all present and returned 200 — spot-checking the obvious
URLs would have passed. So this always ends with a full list_objects_v2 vs
os.walk diff on BOTH the key set and the sizes, and prints OK / FAILED.

It is also resume-safe: keys whose remote size already matches are skipped, so
re-running it repairs a partial upload instead of redoing 300MB.

--always <file name> (repeatable) sends every file with that name even when R2
already has one of the same size, and marks it "Cache-Control: no-cache" so
browsers check for a newer copy. For small lists that change in place, such as
models/models.json after a model update.
"""
import json, os, sys, threading, queue
import boto3
from botocore.config import Config

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def content_type(key):
    if key.endswith(".js"):   return "application/javascript"
    if key.endswith(".json"): return "application/json"
    if key.endswith(".jpg") or key.endswith(".jpeg"): return "image/jpeg"
    if key.endswith(".png"):  return "image/png"
    if key.endswith(".glb"):  return "model/gltf-binary"
    return "application/octet-stream"


def listing(s3, bucket, prefix):
    out, tok = {}, None
    while True:
        kw = dict(Bucket=bucket, Prefix=prefix.rstrip("/") + "/")
        if tok:
            kw["ContinuationToken"] = tok
        r = s3.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            out[o["Key"]] = o["Size"]
        if not r.get("IsTruncated"):
            return out
        tok = r["NextContinuationToken"]


def main(src, prefix, threads=24, always=()):
    prefix = prefix.strip("/")
    src = os.path.abspath(src)
    creds = json.load(open(os.path.join(REPO, ".r2creds.json")))
    s3 = boto3.client(
        "s3", endpoint_url=creds["endpoint"],
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        config=Config(signature_version="s3v4", max_pool_connections=threads * 3,
                      retries={"max_attempts": 5, "mode": "standard"}))
    bucket = creds["bucket"]

    local = {}
    for dirpath, _, files in os.walk(src):
        for f in files:
            fp = os.path.join(dirpath, f)
            rel = os.path.relpath(fp, src).replace("\\", "/")
            local["%s/%s" % (prefix, rel)] = (fp, os.path.getsize(fp))
    print("local files: %d  bytes: %s" % (len(local), format(sum(v[1] for v in local.values()), ",")), flush=True)

    remote = listing(s3, bucket, prefix)
    print("already on R2: %d" % len(remote), flush=True)

    always = set(always)
    todo = [k for k, (fp, sz) in local.items() if remote.get(k) != sz or k.rsplit("/", 1)[-1] in always]
    print("to upload: %d" % len(todo), flush=True)

    q = queue.Queue()
    for k in todo:
        q.put(k)
    done, err, lock = [0], [], threading.Lock()

    def worker():
        while True:
            try:
                k = q.get_nowait()
            except queue.Empty:
                return
            fp, _ = local[k]
            try:
                extra = {"ContentType": content_type(k)}
                if k.rsplit("/", 1)[-1] in always:
                    extra["CacheControl"] = "no-cache"
                s3.upload_file(fp, bucket, k, ExtraArgs=extra)
                with lock:
                    done[0] += 1
                    if done[0] % 250 == 0 or done[0] == len(todo):
                        print("  %d/%d" % (done[0], len(todo)), flush=True)
            except Exception as e:
                with lock:
                    err.append((k, repr(e)))
            finally:
                q.task_done()

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
    for t in ts: t.start()
    for t in ts: t.join()

    if err:
        print("ERRORS: %d" % len(err), flush=True)
        for k, e in err[:10]:
            print("  ", k, e, flush=True)

    # ---- VERIFY: never trust the upload loop's own success count ----
    remote = listing(s3, bucket, prefix)
    missing = [k for k in local if k not in remote]
    mismatch = [k for k in local if k in remote and remote[k] != local[k][1]]
    extra = [k for k in remote if k not in local]
    print("VERIFY local=%d remote=%d missing=%d size_mismatch=%d extra=%d"
          % (len(local), len(remote), len(missing), len(mismatch), len(extra)), flush=True)
    for k in (missing + mismatch)[:10]:
        print("  BAD", k, flush=True)
    ok = not (missing or mismatch or err)
    print("RESULT:", "OK" if ok else "FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    args, always, a = [], [], sys.argv[1:]
    while a:
        if a[0] == "--always" and len(a) > 1:
            always.append(a[1])
            a = a[2:]
        else:
            args.append(a.pop(0))
    if len(args) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], args[1], always=always))
