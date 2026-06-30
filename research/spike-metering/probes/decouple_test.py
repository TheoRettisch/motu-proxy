import http.client, threading, time
HOST="10.0.8.98"
def conn(): return http.client.HTTPConnection(HOST,80,timeout=25)
c=conn(); c.request("GET","/datastore/uid"); r=c.getresponse(); ds_etag=r.getheader("ETag"); r.read(); c.close()
print(f"datastore etag={ds_etag}")
hold={}
def held_longpoll():
    cc=conn(); t0=time.monotonic()
    cc.request("GET","/datastore", headers={"If-None-Match": ds_etag})
    r=cc.getresponse(); b=r.read()
    hold.update(dt=time.monotonic()-t0, status=r.status, nbytes=len(b)); cc.close()
th=threading.Thread(target=held_longpoll); th.start()
time.sleep(0.3)
mc=conn(); lat=[]; t_start=time.monotonic()
for _ in range(30):
    t0=time.monotonic()
    mc.request("GET","/meters?meters=ext/input"); r=mc.getresponse(); r.read()
    lat.append((time.monotonic()-t0)*1000)
window=time.monotonic()-t_start; mc.close(); th.join(); lat.sort()
print(f"datastore long-poll: held={hold.get('dt',0):.2f}s status={hold.get('status')} (~15s=held, <1s=no-hold)")
print(f"meters during hold: n={len(lat)} total_window={window:.2f}s avg={sum(lat)/len(lat):.2f}ms p50={lat[len(lat)//2]:.2f}ms max={max(lat):.2f}ms")
