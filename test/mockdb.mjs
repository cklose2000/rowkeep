// In-memory stand-in for the artifact `db` capability: the subset the store uses.
// Documents are deep-copied on write and read, writes are last-writer-wins, and
// `failAfter` makes the Nth write from now throw, to simulate a crash mid-operation.
export function mockDb() {
  const docs = new Map();
  const leases = new Map();
  const listeners = new Set();
  const state = { writes: 0, failAt: 0 };
  const clone = (x) => JSON.parse(JSON.stringify(x));

  function check(path, even) {
    const segs = path.split("/");
    if ((segs.length % 2 === 0) !== even) throw new TypeError("bad path parity: " + path);
    for (const s of segs) if (!/^[A-Za-z0-9_\-.~:@+]{1,200}$/.test(s)) throw new TypeError("bad segment: " + s);
  }
  function write(fn) {
    state.writes++;
    if (state.failAt && state.writes >= state.failAt) {
      state.failAt = 0;
      const e = new Error("simulated crash");
      e.code = "unavailable";
      throw e;
    }
    fn();
    for (const l of listeners) l();
  }
  function snapOf(path) {
    const id = path.split("/").pop();
    const has = docs.has(path);
    const data = has ? clone(docs.get(path)) : undefined;
    return { id, exists: has, data: () => data, metadata: { fromCache: false, hasPendingWrites: false } };
  }

  function docRef(path) {
    check(path, true);
    return {
      id: path.split("/").pop(),
      path,
      async get() { return snapOf(path); },
      async set(data) {
        if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("invalid_argument");
        if (JSON.stringify(data).length > 262144) {
          const e = new Error("document over 256 KiB"); e.code = "invalid_argument"; throw e;
        }
        write(() => docs.set(path, clone(data)));
      },
      async update(data) {
        if (!docs.has(path)) { const e = new Error("no such doc"); e.code = "invalid_argument"; throw e; }
        write(() => docs.set(path, Object.assign(docs.get(path), clone(data))));
      },
      async delete() { write(() => docs.delete(path)); },
      async acquire({ holder, ttlMs }) {
        const now = Date.now(), cur = leases.get(path);
        if (cur && cur.until > now && cur.holder !== holder) {
          return { acquired: false, expiresAt: new Date(cur.until).toISOString() };
        }
        const until = now + Math.min(600000, Math.max(1000, ttlMs || 30000));
        leases.set(path, { holder, until });
        return { acquired: true, holder, expiresAt: new Date(until).toISOString(), version: 1 };
      },
      onSnapshot(next) {
        const l = () => next(snapOf(path));
        listeners.add(l);
        queueMicrotask(l);
        return () => listeners.delete(l);
      },
      collection(sub) { return colRef(path + "/" + sub); },
    };
  }

  function colRef(path, q = { where: [], order: null, limit: 0 }) {
    check(path, false);
    const depth = path.split("/").length + 1;
    function run() {
      let rows = [];
      for (const [p, d] of docs) {
        if (p.startsWith(path + "/") && p.split("/").length === depth) rows.push({ path: p, id: p.split("/").pop(), d });
      }
      for (const [f, op, v] of q.where) {
        rows = rows.filter(({ d }) => {
          const x = d[f];
          if (x === undefined) return false;
          if (op === "==") return x === v;
          if (op === "array-contains") return Array.isArray(x) && x.includes(v);
          if (op === ">") return x > v;
          if (op === ">=") return x >= v;
          if (op === "<") return x < v;
          if (op === "<=") return x <= v;
          throw new Error("unsupported op " + op);
        });
      }
      if (q.order) {
        const [f, dir] = q.order;
        rows.sort((x, y) => (x.d[f] < y.d[f] ? -1 : x.d[f] > y.d[f] ? 1 : 0) * (dir === "desc" ? -1 : 1));
      } else rows.sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
      if (q.limit) rows = rows.slice(0, q.limit);
      const out = rows.map((r) => snapOf(r.path));
      return { docs: out, size: out.length, empty: !out.length, docChanges: () => [], metadata: { fromCache: false, hasPendingWrites: false } };
    }
    return {
      path,
      where(f, op, v) { return colRef(path, { ...q, where: [...q.where, [f, op, v]] }); },
      orderBy(f, dir) { return colRef(path, { ...q, order: [f, dir || "asc"] }); },
      limit(n) {
        if (!(n >= 1 && n <= 1000)) throw new Error("limit out of range");
        return colRef(path, { ...q, limit: n });
      },
      async get() { return run(); },
      onSnapshot(next) {
        const l = () => next(run());
        listeners.add(l);
        queueMicrotask(l);
        return () => listeners.delete(l);
      },
      doc(id) { return docRef(path + "/" + (id || Math.random().toString(36).slice(2))); },
      async add(data) { const r = this.doc(); await r.set(data); return r; },
    };
  }

  return {
    doc: docRef,
    collection: (p) => colRef(p),
    _docs: docs,
    _state: state,
    _failAfter(n) { state.failAt = state.writes + n; },
    _count(prefix) { let n = 0; for (const p of docs.keys()) if (p.startsWith(prefix)) n++; return n; },
  };
}
