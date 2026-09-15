namespace Ego.Demo
{
    public static class EgoProtocol
    {
        public const int DefaultPort = 9100;
        public const float HeartbeatIntervalSeconds = 10f;
        public const int MaxFrameBytes = 1 << 20;

        public const string Heartbeat = "heartbeat";
        public const string HeartbeatAck = "heartbeat_ack";
        public const string GetDeviceStatus = "get_device_status";
        public const string DeviceInfo = "device_info";
        public const string StartRecording = "start_recording";
        public const string StopAndSaveRecording = "stop_and_save_recording";
        public const string ChangeView = "change_view";
        public const string ViewChanged = "view_changed";
        public const string CaptureResult = "capture_result";
        public const string Error = "error";

        public static string ViewIdFromNum(int viewNum)
        {
            switch (viewNum)
            {
                case 1: return "front_left";
                case 2: return "front_right";
                case 3: return "rear_left";
                case 4: return "rear_right";
                default: return "front_left";
            }
        }
    }
}
