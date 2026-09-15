using System;
using UnityEngine;

namespace Ego.Demo
{
    [Serializable]
    public class DeviceInfoData
    {
        public string state;
        public int duration;
        public int segment_count;
        public string storage_free;
        public float storage_free_gb;
        public bool storage_warn;
        public bool frames_writing;
        public int view_num;
        public string view_id;
        public string preview_mjpeg_url;
        public string preview_jpg_url;
        public string msg;
    }

    [Serializable]
    public class ViewChangedData
    {
        public int view_num;
        public string view_id;
        public string preview_mjpeg_url;
        public string preview_jpg_url;
    }

    [Serializable]
    public class CaptureResultData
    {
        public string action;
        public bool success;
        public string msg;
    }

    [Serializable]
    public class ErrorData
    {
        public string code;
        public string message;
        public string request_type;
    }

    [Serializable]
    class WireEnvelope
    {
        public string type;
    }

    [Serializable]
    class DeviceInfoWire
    {
        public string type;
        public DeviceInfoData data;
    }

    [Serializable]
    class ViewChangedWire
    {
        public string type;
        public ViewChangedData data;
    }

    [Serializable]
    class CaptureResultWire
    {
        public string type;
        public CaptureResultData data;
    }

    [Serializable]
    class ErrorWire
    {
        public string type;
        public ErrorData data;
    }

    public readonly struct EgoParsedMessage
    {
        public readonly string Type;
        public readonly string RawJson;
        public readonly DeviceInfoData DeviceInfo;
        public readonly ViewChangedData ViewChanged;
        public readonly CaptureResultData CaptureResult;
        public readonly ErrorData Error;

        public EgoParsedMessage(
            string type,
            string rawJson,
            DeviceInfoData deviceInfo = null,
            ViewChangedData viewChanged = null,
            CaptureResultData captureResult = null,
            ErrorData error = null)
        {
            Type = type;
            RawJson = rawJson;
            DeviceInfo = deviceInfo;
            ViewChanged = viewChanged;
            CaptureResult = captureResult;
            Error = error;
        }
    }

    public static class EgoMessageParser
    {
        public static EgoParsedMessage Parse(string json)
        {
            var header = JsonUtility.FromJson<WireEnvelope>(json);
            var type = header?.type ?? string.Empty;

            switch (type)
            {
                case EgoProtocol.DeviceInfo:
                    var info = JsonUtility.FromJson<DeviceInfoWire>(json);
                    return new EgoParsedMessage(type, json, info?.data);
                case EgoProtocol.ViewChanged:
                    var view = JsonUtility.FromJson<ViewChangedWire>(json);
                    return new EgoParsedMessage(type, json, viewChanged: view?.data);
                case EgoProtocol.CaptureResult:
                    var cap = JsonUtility.FromJson<CaptureResultWire>(json);
                    return new EgoParsedMessage(type, json, captureResult: cap?.data);
                case EgoProtocol.Error:
                    var err = JsonUtility.FromJson<ErrorWire>(json);
                    return new EgoParsedMessage(type, json, error: err?.data);
                default:
                    return new EgoParsedMessage(type, json);
            }
        }

        public static string Build(string type, string dataJson = null)
        {
            if (string.IsNullOrEmpty(dataJson))
                return $"{{\"type\":\"{type}\"}}";
            return $"{{\"type\":\"{type}\",\"data\":{dataJson}}}";
        }

        public static string BuildChangeView(int viewNum) =>
            Build(EgoProtocol.ChangeView, $"{{\"view_num\":{viewNum}}}");
    }
}
