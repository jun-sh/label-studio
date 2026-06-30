/**
 * DLB1 frame bin unpack (mirrors ego_capture_studio/capture/frame_bin_codec.py).
 */
const FRAME_BIN_MAGIC = Buffer.from("DLB1");
const FRAME_BIN_VERSION = 1;
const HEADER_SIZE = 6; // magic(4) + version(1) + n_keys(1)
const ENTRY_HEADER_SIZE = 6; // key_len(2) + jpeg_len(4)

/**
 * @param {Buffer} data
 * @returns {Record<string, Buffer>}
 */
export function unpackFrameBin(data) {
  if (!Buffer.isBuffer(data) || data.length < HEADER_SIZE) {
    throw new Error("frame bin too short");
  }
  if (!data.subarray(0, 4).equals(FRAME_BIN_MAGIC)) {
    throw new Error(`bad frame bin magic: ${data.subarray(0, 4).toString()}`);
  }
  const version = data.readUInt8(4);
  if (version !== FRAME_BIN_VERSION) {
    throw new Error(`unsupported frame bin version: ${version}`);
  }
  const nKeys = data.readUInt8(5);
  let offset = HEADER_SIZE;
  /** @type {Record<string, Buffer>} */
  const out = {};
  for (let i = 0; i < nKeys; i += 1) {
    if (offset + ENTRY_HEADER_SIZE > data.length) {
      throw new Error("truncated frame bin entry header");
    }
    const keyLen = data.readUInt16LE(offset);
    const jpegLen = data.readUInt32LE(offset + 2);
    offset += ENTRY_HEADER_SIZE;
    const endKey = offset + keyLen;
    const endJpeg = endKey + jpegLen;
    if (endJpeg > data.length) {
      throw new Error("truncated frame bin entry payload");
    }
    const key = data.subarray(offset, endKey).toString("utf8");
    const jpeg = data.subarray(endKey, endJpeg);
    if (jpeg.length > 0) {
      out[key] = jpeg;
    }
    offset = endJpeg;
  }
  return out;
}
