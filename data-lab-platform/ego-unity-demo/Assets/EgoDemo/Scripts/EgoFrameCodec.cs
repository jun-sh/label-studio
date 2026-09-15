using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Ego.Demo
{
    public static class EgoFrameCodec
    {
        public static byte[] Encode(string json)
        {
            var body = Encoding.UTF8.GetBytes(json);
            if (body.Length > EgoProtocol.MaxFrameBytes)
                throw new InvalidOperationException($"frame too large: {body.Length}");

            var frame = new byte[4 + body.Length];
            frame[0] = (byte)((body.Length >> 24) & 0xFF);
            frame[1] = (byte)((body.Length >> 16) & 0xFF);
            frame[2] = (byte)((body.Length >> 8) & 0xFF);
            frame[3] = (byte)(body.Length & 0xFF);
            Buffer.BlockCopy(body, 0, frame, 4, body.Length);
            return frame;
        }

        public static List<string> Feed(List<byte> buffer)
        {
            var messages = new List<string>();
            while (true)
            {
                if (buffer.Count < 4)
                    break;

                var length =
                    (buffer[0] << 24) |
                    (buffer[1] << 16) |
                    (buffer[2] << 8) |
                    buffer[3];

                if (length <= 0 || length > EgoProtocol.MaxFrameBytes)
                    throw new InvalidDataException($"invalid frame length: {length}");

                if (buffer.Count < 4 + length)
                    break;

                var json = Encoding.UTF8.GetString(buffer.GetRange(4, length).ToArray());
                buffer.RemoveRange(0, 4 + length);
                messages.Add(json);
            }

            return messages;
        }
    }
}
