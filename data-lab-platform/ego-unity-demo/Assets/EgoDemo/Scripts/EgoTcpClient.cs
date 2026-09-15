using System;
using System.Collections.Generic;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

namespace Ego.Demo
{
    public class EgoTcpClient : IDisposable
    {
        readonly object _sendLock = new object();
        readonly Queue<EgoParsedMessage> _inbox = new Queue<EgoParsedMessage>();
        readonly Queue<string> _logQueue = new Queue<string>();
        readonly List<byte> _rxBuffer = new List<byte>();

        TcpClient _client;
        NetworkStream _stream;
        Thread _readerThread;
        volatile bool _running;
        volatile bool _disconnectPending;

        public bool IsConnected => _running && _client != null && _client.Connected;

        public event Action Connected;
        public event Action Disconnected;
        public event Action<string> Log;
        public event Action<Exception> ConnectionFailed;

        public void Connect(string host, int port)
        {
            Disconnect();
            try
            {
                _client = new TcpClient();
                _client.NoDelay = true;
                _client.Connect(host, port);
                _stream = _client.GetStream();
                _running = true;
                _readerThread = new Thread(ReadLoop) { IsBackground = true, Name = "EgoTcpReader" };
                _readerThread.Start();
                Log?.Invoke($"connected {host}:{port}");
                Connected?.Invoke();
            }
            catch (Exception ex)
            {
                ConnectionFailed?.Invoke(ex);
                Disconnect();
                throw;
            }
        }

        public void Disconnect()
        {
            _running = false;
            try { _stream?.Close(); } catch { /* ignore */ }
            try { _client?.Close(); } catch { /* ignore */ }
            _stream = null;
            _client = null;
            _rxBuffer.Clear();
            lock (_inbox)
                _inbox.Clear();
            _disconnectPending = false;
            Disconnected?.Invoke();
        }

        public void PumpMainThread()
        {
            while (true)
            {
                string line;
                lock (_logQueue)
                {
                    if (_logQueue.Count == 0)
                        break;
                    line = _logQueue.Dequeue();
                }
                Log?.Invoke(line);
            }

            if (_disconnectPending)
            {
                _disconnectPending = false;
                Disconnect();
            }
        }

        public void SendType(string type, string dataJson = null)
        {
            if (!IsConnected || _stream == null)
                throw new InvalidOperationException("not connected");

            var json = EgoMessageParser.Build(type, dataJson);
            var frame = EgoFrameCodec.Encode(json);
            lock (_sendLock)
                _stream.Write(frame, 0, frame.Length);
            EnqueueLog($"→ {json}");
        }

        void EnqueueLog(string line)
        {
            lock (_logQueue)
                _logQueue.Enqueue(line);
        }

        public bool TryDequeue(out EgoParsedMessage message)
        {
            lock (_inbox)
            {
                if (_inbox.Count == 0)
                {
                    message = default;
                    return false;
                }

                message = _inbox.Dequeue();
                return true;
            }
        }

        void ReadLoop()
        {
            var chunk = new byte[8192];
            try
            {
                while (_running && _stream != null)
                {
                    var read = _stream.Read(chunk, 0, chunk.Length);
                    if (read <= 0)
                        break;

                    lock (_rxBuffer)
                    {
                        for (var i = 0; i < read; i++)
                            _rxBuffer.Add(chunk[i]);

                        var messages = EgoFrameCodec.Feed(_rxBuffer);
                        foreach (var json in messages)
                        {
                            var parsed = EgoMessageParser.Parse(json);
                            lock (_inbox)
                                _inbox.Enqueue(parsed);
                            EnqueueLog($"← {json}");
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                if (_running)
                    EnqueueLog($"read error: {ex.Message}");
            }
            finally
            {
                if (_running)
                    _disconnectPending = true;
                _running = false;
            }
        }

        public void Dispose() => Disconnect();
    }
}
