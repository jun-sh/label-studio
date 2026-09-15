using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace Ego.Demo
{
    public class EgoDemoApp : MonoBehaviour
    {
        [Header("Defaults")]
        [SerializeField] string defaultHost = "10.10.10.130";
        [SerializeField] int defaultPort = EgoProtocol.DefaultPort;
        [SerializeField] float previewPollSeconds = 0.5f;

        EgoTcpClient _client;
        DeviceInfoData _lastInfo;
        string _previewUrl;
        float _heartbeatTimer;
        bool _busy;
        readonly StringBuilder _log = new StringBuilder();

        InputField _hostInput;
        InputField _portInput;
        Text _statusText;
        Text _logText;
        RawImage _previewImage;
        Texture2D _previewTexture;
        Button _connectButton;
        Button _startButton;
        Button _stopButton;

        Coroutine _autoConnectRoutine;

        void Awake()
        {
            _client = new EgoTcpClient();
            _client.Log += AppendLog;
            _client.Connected += OnConnected;
            _client.Disconnected += OnDisconnected;
            _client.ConnectionFailed += ex => AppendLog($"connect failed: {ex.Message}");
        }

        void OnDestroy()
        {
            _client?.Dispose();
            if (_previewTexture != null)
                Destroy(_previewTexture);
        }

        void Update()
        {
            _client.PumpMainThread();

            while (_client.TryDequeue(out var msg))
                HandleMessage(msg);

            if (_client.IsConnected)
            {
                _heartbeatTimer += Time.deltaTime;
                if (_heartbeatTimer >= EgoProtocol.HeartbeatIntervalSeconds)
                {
                    _heartbeatTimer = 0f;
                    SafeSend(EgoProtocol.Heartbeat);
                }
            }

            UpdateStatusPanel();
        }

        public void ScheduleAutoConnect(float delaySeconds)
        {
            if (_autoConnectRoutine != null)
                StopCoroutine(_autoConnectRoutine);
            _autoConnectRoutine = StartCoroutine(AutoConnectAfter(delaySeconds));
        }

        IEnumerator AutoConnectAfter(float delaySeconds)
        {
            yield return new WaitForSeconds(delaySeconds);
            if (!_client.IsConnected)
                OnConnectClicked();
        }

        public void BindUi(
            InputField hostInput,
            InputField portInput,
            Text statusText,
            Text logText,
            RawImage previewImage,
            Button connectButton,
            Button startButton,
            Button stopButton)
        {
            _hostInput = hostInput;
            _portInput = portInput;
            _statusText = statusText;
            _logText = logText;
            _previewImage = previewImage;
            _connectButton = connectButton;
            _startButton = startButton;
            _stopButton = stopButton;

            if (_hostInput != null)
                _hostInput.text = defaultHost;
            if (_portInput != null)
                _portInput.text = defaultPort.ToString();
        }

        public void OnConnectClicked()
        {
            if (_client.IsConnected)
            {
                _client.Disconnect();
                StopAllCoroutines();
                return;
            }

            var host = _hostInput != null ? _hostInput.text.Trim() : defaultHost;
            if (!int.TryParse(_portInput != null ? _portInput.text.Trim() : defaultPort.ToString(), out var port))
                port = defaultPort;

            try
            {
                _client.Connect(host, port);
            }
            catch (Exception ex)
            {
                AppendLog(ex.Message);
            }
        }

        public void OnRefreshStatusClicked() => SafeSend(EgoProtocol.GetDeviceStatus);

        public void OnStartRecordingClicked()
        {
            if (_busy) return;
            _busy = true;
            SetButtonsInteractable(true);
            SafeSend(EgoProtocol.StartRecording);
        }

        public void OnStopRecordingClicked()
        {
            if (_busy) return;
            _busy = true;
            SetButtonsInteractable(true);
            SafeSend(EgoProtocol.StopAndSaveRecording);
        }

        public void OnChangeViewClicked(int viewNum)
        {
            if (!_client.IsConnected) return;
            try
            {
                _client.SendType(EgoProtocol.ChangeView, $"{{\"view_num\":{viewNum}}}");
            }
            catch (Exception ex)
            {
                AppendLog(ex.Message);
            }
        }

        void OnConnected()
        {
            _heartbeatTimer = 0f;
            SetConnectButtonLabel("Disconnect");
            SetButtonsInteractable(true);
        }

        void OnDisconnected()
        {
            _lastInfo = null;
            _previewUrl = null;
            _busy = false;
            _heartbeatTimer = 0f;
            SetConnectButtonLabel("Connect");
            SetButtonsInteractable(false);
            AppendLog("disconnected");
        }

        void HandleMessage(EgoParsedMessage msg)
        {
            switch (msg.Type)
            {
                case EgoProtocol.DeviceInfo:
                    _lastInfo = msg.DeviceInfo;
                    if (_lastInfo != null && !string.IsNullOrEmpty(_lastInfo.preview_jpg_url))
                    {
                        _previewUrl = _lastInfo.preview_jpg_url;
                        RestartPreviewPolling();
                    }
                    break;
                case EgoProtocol.ViewChanged:
                    if (msg.ViewChanged != null && !string.IsNullOrEmpty(msg.ViewChanged.preview_jpg_url))
                    {
                        _previewUrl = msg.ViewChanged.preview_jpg_url;
                        RestartPreviewPolling();
                    }
                    break;
                case EgoProtocol.CaptureResult:
                    _busy = false;
                    SetButtonsInteractable(true);
                    if (msg.CaptureResult != null)
                        AppendLog($"capture {msg.CaptureResult.action}: success={msg.CaptureResult.success} msg={msg.CaptureResult.msg}");
                    break;
                case EgoProtocol.Error:
                    _busy = false;
                    SetButtonsInteractable(true);
                    if (msg.Error != null)
                        AppendLog($"error [{msg.Error.code}] {msg.Error.message}");
                    break;
            }
        }

        void SafeSend(string type)
        {
            if (!_client.IsConnected) return;
            try
            {
                _client.SendType(type);
            }
            catch (Exception ex)
            {
                AppendLog(ex.Message);
            }
        }

        void RestartPreviewPolling()
        {
            StopCoroutine(nameof(PreviewLoop));
            if (!string.IsNullOrEmpty(_previewUrl))
                StartCoroutine(PreviewLoop());
        }

        IEnumerator PreviewLoop()
        {
            while (_client.IsConnected && !string.IsNullOrEmpty(_previewUrl))
            {
                using (var req = UnityWebRequestTexture.GetTexture(_previewUrl + "?t=" + DateTime.UtcNow.Ticks))
                {
                    req.timeout = 3;
                    yield return req.SendWebRequest();
#if UNITY_2020_2_OR_NEWER
                    if (req.result == UnityWebRequest.Result.Success)
#else
                    if (!req.isNetworkError && !req.isHttpError)
#endif
                    {
                        var tex = DownloadHandlerTexture.GetContent(req);
                        if (_previewTexture != null)
                            Destroy(_previewTexture);
                        _previewTexture = tex;
                        if (_previewImage != null)
                            _previewImage.texture = _previewTexture;
                    }
                }
                yield return new WaitForSeconds(previewPollSeconds);
            }
        }

        void UpdateStatusPanel()
        {
            if (_statusText == null) return;
            if (_lastInfo == null)
            {
                _statusText.text = _client.IsConnected ? "Connected — waiting for device_info…" : "Not connected";
                return;
            }

            _statusText.text =
                $"state: {_lastInfo.state}\n" +
                $"duration: {_lastInfo.duration}s\n" +
                $"segments: {_lastInfo.segment_count}\n" +
                $"storage: {_lastInfo.storage_free} ({_lastInfo.storage_free_gb:F1} GB)\n" +
                $"view: {_lastInfo.view_num} ({_lastInfo.view_id})\n" +
                $"writing: {_lastInfo.frames_writing}\n" +
                $"msg: {_lastInfo.msg}";
        }

        void AppendLog(string line)
        {
            var stamp = DateTime.Now.ToString("HH:mm:ss");
            _log.Insert(0, $"[{stamp}] {line}\n");
            if (_log.Length > 8000)
                _log.Length = 8000;
            if (_logText != null)
                _logText.text = _log.ToString();
        }

        void SetConnectButtonLabel(string label)
        {
            if (_connectButton == null) return;
            var text = _connectButton.GetComponentInChildren<Text>();
            if (text != null)
                text.text = label;
        }

        void SetButtonsInteractable(bool connected)
        {
            if (_startButton != null)
                _startButton.interactable = connected && !_busy;
            if (_stopButton != null)
                _stopButton.interactable = connected && !_busy;
        }
    }
}
