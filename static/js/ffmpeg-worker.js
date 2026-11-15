// Module worker hosting the ffmpeg.wasm core (@ffmpeg/core, single-threaded build).
// Inputs are mounted with WORKERFS so large Blobs are read lazily instead of being
// copied into memory up front.

let core = null;

async function load({ coreURL, wasmURL, wasmModule }) {
  const { default: createFFmpegCore } = await import(coreURL);
  const opts = {
    // ffmpeg.wasm locates the .wasm via a JSON blob after '#' in this URL.
    mainScriptUrlOrBlob: `${coreURL}#${btoa(JSON.stringify({ wasmURL }))}`,
  };
  if (wasmModule) {
    // Re-use a module the page already compiled: new workers start in milliseconds.
    opts.instantiateWasm = (imports, done) => {
      WebAssembly.instantiate(wasmModule, imports).then((inst) => done(inst, wasmModule));
      return {};
    };
  }
  core = await createFFmpegCore(opts);
  core.setLogger((d) => self.postMessage({ type: "log", payload: d }));
  core.setProgress((d) => self.postMessage({ type: "progress", payload: d }));
  return true;
}

const handlers = {
  load,
  mount({ dir, blobs }) {
    try { core.FS.mkdir(dir); } catch { /* exists */ }
    core.FS.mount(core.FS.filesystems.WORKERFS, { blobs }, dir);
    return true;
  },
  unmount({ dir }) { core.FS.unmount(dir); return true; },
  exec({ args }) {
    core.setTimeout(-1);
    core.exec(...args);
    const ret = core.ret;
    core.reset();
    return ret;
  },
  read({ path }) { return core.FS.readFile(path); },
  unlink({ path }) { try { core.FS.unlink(path); } catch { /* gone */ } return true; },
};

self.onmessage = async ({ data: { id, type, payload } }) => {
  try {
    if (type !== "load" && !core) throw new Error("ffmpeg not loaded");
    const result = await handlers[type](payload);
    const transfer = result instanceof Uint8Array ? [result.buffer] : [];
    self.postMessage({ id, type: "ok", payload: result }, transfer);
  } catch (e) {
    self.postMessage({ id, type: "error", payload: String(e && e.message || e) });
  }
};
