using UnityEngine;

namespace Ego.Demo
{
    /// <summary>
    /// Builds the demo UI at runtime — no Editor menu required. Press Play on any scene.
    /// </summary>
    [DefaultExecutionOrder(-100)]
    public class EgoDemoRuntimeBootstrap : MonoBehaviour
    {
        [SerializeField] bool autoConnectOnPlay;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        static void AutoCreateIfMissing()
        {
            if (Object.FindObjectOfType<EgoDemoApp>() != null)
                return;

            var go = new GameObject("EgoDemo");
            go.AddComponent<EgoDemoRuntimeBootstrap>();
        }

        void Awake()
        {
            if (GetComponent<EgoDemoApp>() == null)
                gameObject.AddComponent<EgoDemoApp>();

            var app = GetComponent<EgoDemoApp>();
            var ui = EgoDemoUiFactory.Build();

            app.BindUi(
                ui.HostInput,
                ui.PortInput,
                ui.StatusText,
                ui.LogText,
                ui.PreviewImage,
                ui.ConnectButton,
                ui.StartButton,
                ui.StopButton);

            ui.ConnectButton.onClick.AddListener(app.OnConnectClicked);
            ui.RefreshButton.onClick.AddListener(app.OnRefreshStatusClicked);
            ui.StartButton.onClick.AddListener(app.OnStartRecordingClicked);
            ui.StopButton.onClick.AddListener(app.OnStopRecordingClicked);
            for (var i = 0; i < ui.ViewButtons.Length; i++)
            {
                var viewNum = i + 1;
                ui.ViewButtons[i].onClick.AddListener(() => app.OnChangeViewClicked(viewNum));
            }

            if (autoConnectOnPlay)
                app.ScheduleAutoConnect(0.5f);
        }
    }
}
