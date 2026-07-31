/* hyperframes: animation, plaintext-video-sequencer
 * Scheleton: { fps, width, height, col, bg, title, frames }
 *
 * Optional (if ffmpeg/vosk/speech are loaded):
 *  - PROMPT mode: VOICEPROMPT / VOICEIN / VOICEOVER
 *  - Transcode/SCENE for inline rendering
 */

import { initialize, set_pixel } from "./base.js";
import { prompt } from "./prompt.js";

// ── helpers ──────────────────────────────────────────────────────────
function canvas_size(w, h) {
  if (Array.isArray(w)) return { width: w[0], height: w[1] };
  if (typeof w === "object" && w != null && "width" in w && "height" in w) return w;
  return { width: w || 80, height: h || 24 };
}

function text(piece, spaces, col, bg) {
  if (bg != null) return ANSI_BG[bg]() + cpad(piece, spaces, col, bg) + ANSI_RESET;
  return cpad(piece, spaces, col, bg);
}

function cpad(piece, spaces, col, bg) {
  let p = typeof piece === "string" ? piece : String(piece);
  if (spaces && p.length > spaces) p = p.slice(0, spaces);
  return cpad_left(pad_right(p, spaces), spaces, col, bg);
}

function pad_right(s, n) {
  while (s.length < n) s += " ";
  return s;
}

function cpad_left(s, n, col, bg) {
  const need = Math.max(0, n - s.length);
  const left = Math.floor(need / 2);
  return pad_right("", left) + s;
}

function escape_brackets(str) {
  // Escape "[" used by bash PS1 / readline prompt markers.
  return str.replace("[", "\\[");
}

function render_frame_to_canvas(frame, w, h, col, bg) {
  const len = w * h;
  for (let i = 0; i < len; i++) set_pixel(false, i % w, Math.floor(i / w));
}

function frame_dimensions(frame, defaultWidth, defaultHeight) {
  let w = defaultWidth;
  let h = defaultHeight;
  if (Array.isArray(frame)) {
    w = frame[0] || w;
    h = frame[1] || h;
  } else if (frame && typeof frame === "object" && frame.width && frame.height) {
    w = frame.width;
    h = frame.height;
  }
  return { w, h };
}

function encode(frame, width, height, col, bg) {
  // Public single-frame encoder. Retain exact prior behavior for existing callers.
  const s = text(frame, width, col, bg);
  const lines = [];
  let i = 0;
  while (i < s.length) {
    const row = s.slice(i, i + width);
    lines.push(row.length ? escape_brackets(row) : "");
    i += width;
  }
  return lines.join("\n");
}

function get_frames(data, type = "image", dims = null, col = 7, bg = 0) {
  if (!data || !data.frames) return [];
  return data.frames.map((frame) => encode_frame(frame, dims || data.width || 80, dims ? dims.height : data.height || 24, col, bg));
}

function encode_frame(frame, width, height, col, bg) {
  const { w, h } = frame_dimensions(frame, width, height);
  let view = frame;
  if (Array.isArray(frame) && frame[0] && frame[1]) view = frame.slice(2);
  const frameWidth = w || width;
  const frameHeight = typeof view === "string" ? (view.match(/\n/g) || []).length + 1 : h;
  const size = Math.max(frameWidth, width) * Math.max(frameHeight, height);
  render_frame_to_canvas(view, frameWidth, frameHeight, col, bg);
  return encode(view, frameWidth, frameHeight, col, bg);
}

// ── Hyperframes state flow ───────────────────────────────────
// `video(state)` returns a new clone. This is the main “frame flow” function.
// video() no longer mutates its input. Derived values such as speed and step
// are recomputed on each call. Returns a new state object derived from input.
function video(state) {
  if (!state) throw new Error("video(state) requires a state object");

  const next = Object.assign({}, state);
  next.speed = state.speed || 1;
  next.step = Math.max(1, Math.floor(next.fps * next.speed)) || 1;
  return next;
}

// ── Plain-text video rendering helpers ───────────────────────
function render_piece(piece, col, bg) {
  const w = piece.width || 80;
  const h = piece.height || 24;
  const s = text(piece, w, col, bg);
  return s;
}

function render_pieces(pieces, w, h, col, bg, frame_offset = 0) {
  if (!Array.isArray(pieces)) pieces = [pieces];
  const size = w * h;
  const all = [];
  for (const piece of pieces) all.push(...get_frames(piece || {}, "image", [w, h], col, bg));
  const out = new Array(size).fill(encode(" ", w, h, col, bg));
  const max = Math.min(size, frame_offset + all.length);
  for (let i = frame_offset; i < max; i++) out[i] = all[i - frame_offset] || out[i];
  return out;
}

// ── Skeleton renderers ───────────────────────────────────────
function render_background(w, h, col, bg) {
  return new Array(w * h).fill(0).map(() => encode(" ", w, h, col, bg));
}

function render_overlay(overlay, w, h, col, bg) {
  if (!overlay) return [];
  const size = w * h;
  const out = render_background(w, h, col, bg);
  for (const piece of (overlay.frames || [overlay])) {
    const frame = encode_frame(piece, w, h, col, bg);
    for (let i = 0; i < Math.min(size, frame.length); i++) out[i] = frame[i] || out[i];
  }
  return out;
}

// hyperframes.animate is the public entrypoint for the old async animation API.
// It consumes the frame flow shape: (state) -> frameRender -> nextState
async function animate({ piece, type = "image", col = 7, bg = 0, duration, repeats = 1, startBlobs }) {
  const dims = canvas_size(piece?.width || 80, piece?.height || 24);
  const w = dims.width;
  const h = dims.height;

  initialize(w, h, bg);

  const frames = get_frames(piece || {}, type, dims, col, bg);
  if (!frames.length) return;

  const totalMs = Math.max(1, Math.round((duration || 1) * 1000));
  const msPerFrame = Math.max(1, Math.round(totalMs / frames.length));
  const state = video({ fps: 1000 / msPerFrame, width: w, height: h });
  for (let r = 0; r < repeats; r++) {
    for (const frame of frames) {
      write_to_terminal(frame);
      await sleep(msPerFrame);
    }
  }
}

function write_to_terminal(frame) {
  // Hand off to terminal escape output.
  // The writer handles the attached continue/inject behavior on its own.
  if (typeof process !== "undefined" && process.stdout && typeof process.stdout.write === "function") {
    try {
      process.stdout.write(frame + "\n");
      return;
    } catch (_) {}
  }
}

export async function video_v2(data, col = 7, bg = 0, fps = 10, repeats = 1) {
  if (!data || !data.frames || !data.frames.length) return;
  const w = data.width || 80;
  const h = data.height || 24;
  initialize(w, h, bg);
  const frames = get_frames(data, "image", [w, h], col, bg);
  const msPerFrame = Math.max(1, Math.round(1000 / Math.max(1, fps)));
  for (let r = 0; r < repeats; r++) {
    for (const frame of frames) {
      write_to_terminal(frame);
      await new Promise((resolve) => setTimeout(resolve, msPerFrame));
    }
  }
}

const ANSI_RESET = "\u001b[0m";
const ANSI_BG = {
  0: () => "\u001b[48;5;16m",
  1: () => "\u001b[48;5;88m",
  2: () => "\u001b[48;5;22m",
  3: () => "\u001b[48;5;18m",
  4: () => "\u001b[48;5;52m",
  5: () => "\u001b[48;5;17m",
  6: () => "\u001b[48;5;54m",
  7: () => "\u001b[48;5;235m",
};
