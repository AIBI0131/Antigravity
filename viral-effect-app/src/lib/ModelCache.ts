/**
 * ModelCache — IndexedDB-backed LRU cache for ONNX model files.
 * Prevents redundant downloads on repeat visits.
 */

const DB_NAME = 'viral-effect-models';
const DB_VERSION = 1;
const STORE_NAME = 'models';
const META_STORE = 'meta';

interface ModelEntry {
  id: string;
  data: ArrayBuffer;
  sizeMB: number;
  accessedAt: number;
}

interface MetaEntry {
  key: string;
  value: unknown;
}

let dbPromise: Promise<IDBDatabase> | null = null;

function openDB(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;

  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);

    req.onupgradeneeded = (e) => {
      const db = (e.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains(META_STORE)) {
        db.createObjectStore(META_STORE, { keyPath: 'key' });
      }
    };

    req.onsuccess = (e) => resolve((e.target as IDBOpenDBRequest).result);
    req.onerror = (e) => {
      dbPromise = null;
      reject((e.target as IDBOpenDBRequest).error);
    };
  });

  return dbPromise;
}

function tx(
  db: IDBDatabase,
  stores: string | string[],
  mode: IDBTransactionMode
): IDBTransaction {
  return db.transaction(stores, mode);
}

function promisify<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export class ModelCache {
  async get(modelId: string): Promise<ArrayBuffer | null> {
    const db = await openDB();
    const t = tx(db, STORE_NAME, 'readwrite');
    const store = t.objectStore(STORE_NAME);
    const entry = await promisify<ModelEntry | undefined>(store.get(modelId));

    if (!entry) return null;

    // Update LRU timestamp
    entry.accessedAt = Date.now();
    store.put(entry);

    return entry.data;
  }

  async set(modelId: string, data: ArrayBuffer, sizeMB: number): Promise<void> {
    const db = await openDB();
    const t = tx(db, STORE_NAME, 'readwrite');
    const entry: ModelEntry = {
      id: modelId,
      data,
      sizeMB,
      accessedAt: Date.now()
    };
    await promisify(t.objectStore(STORE_NAME).put(entry));
  }

  async has(modelId: string): Promise<boolean> {
    const db = await openDB();
    const t = tx(db, STORE_NAME, 'readonly');
    const count = await promisify<number>(t.objectStore(STORE_NAME).count(modelId));
    return count > 0;
  }

  async delete(modelId: string): Promise<void> {
    const db = await openDB();
    const t = tx(db, STORE_NAME, 'readwrite');
    await promisify(t.objectStore(STORE_NAME).delete(modelId));
  }

  async getTotalSizeMB(): Promise<number> {
    const db = await openDB();
    const t = tx(db, STORE_NAME, 'readonly');
    const all = await promisify<ModelEntry[]>(t.objectStore(STORE_NAME).getAll());
    return all.reduce((sum, e) => sum + (e.sizeMB ?? 0), 0);
  }

  async listAll(): Promise<Array<{ id: string; sizeMB: number; accessedAt: number }>> {
    const db = await openDB();
    const t = tx(db, STORE_NAME, 'readonly');
    const all = await promisify<ModelEntry[]>(t.objectStore(STORE_NAME).getAll());
    return all.map(({ id, sizeMB, accessedAt }) => ({ id, sizeMB, accessedAt }));
  }

  /**
   * Remove least-recently-used models until total size is under maxSizeMB.
   */
  async cleanup(maxSizeMB: number): Promise<void> {
    const entries = await this.listAll();
    const totalMB = entries.reduce((s, e) => s + e.sizeMB, 0);

    if (totalMB <= maxSizeMB) return;

    // Sort by LRU (oldest access first)
    const sorted = [...entries].sort((a, b) => a.accessedAt - b.accessedAt);
    let remaining = totalMB;

    for (const entry of sorted) {
      if (remaining <= maxSizeMB) break;
      await this.delete(entry.id);
      remaining -= entry.sizeMB;
    }
  }
}

export const modelCache = new ModelCache();
