using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace Ego.Demo
{
    public sealed class EgoDemoUiRefs
    {
        public InputField HostInput;
        public InputField PortInput;
        public Text StatusText;
        public Text LogText;
        public RawImage PreviewImage;
        public Button ConnectButton;
        public Button StartButton;
        public Button StopButton;
        public Button RefreshButton;
        public Button[] ViewButtons;
    }

    public static class EgoDemoUiFactory
    {
        public static EgoDemoUiRefs Build()
        {
            var canvasGo = new GameObject("Canvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            var canvas = canvasGo.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            var scaler = canvasGo.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1280, 720);

            if (Object.FindObjectOfType<EventSystem>() == null)
                new GameObject("EventSystem", typeof(EventSystem), typeof(StandaloneInputModule));

            var root = CreatePanel(canvasGo.transform, "Root", new Vector2(0, 0), new Vector2(1, 1), Vector2.zero, Vector2.zero);
            var layout = root.gameObject.AddComponent<VerticalLayoutGroup>();
            layout.padding = new RectOffset(12, 12, 12, 12);
            layout.spacing = 8;
            layout.childControlHeight = true;
            layout.childControlWidth = true;
            layout.childForceExpandHeight = false;
            layout.childForceExpandWidth = true;

            var title = CreateText(root, "Ego TCP Demo (130 simulator)", 22, FontStyle.Bold);
            title.gameObject.AddComponent<LayoutElement>().preferredHeight = 36;

            var connRow = CreateHorizontal(root);
            var hostInput = CreateInput(connRow, "10.10.10.130", 260);
            var portInput = CreateInput(connRow, "9100", 80);
            var connectBtn = CreateButton(connRow, "Connect", 120);

            var actionRow = CreateHorizontal(root);
            var refreshBtn = CreateButton(actionRow, "Refresh Status", 140);
            var startBtn = CreateButton(actionRow, "Start Recording", 150);
            var stopBtn = CreateButton(actionRow, "Stop & Save", 140);

            var viewRow = CreateHorizontal(root);
            var viewButtons = new Button[4];
            for (var i = 0; i < 4; i++)
                viewButtons[i] = CreateButton(viewRow, $"View {i + 1}", 90);

            var bodyRow = CreateHorizontal(root);
            var bodyLayout = bodyRow.gameObject.AddComponent<HorizontalLayoutGroup>();
            bodyLayout.spacing = 8;
            bodyLayout.childForceExpandWidth = true;
            bodyLayout.childForceExpandHeight = true;

            var leftCol = CreateVertical(bodyRow);
            leftCol.gameObject.AddComponent<LayoutElement>().flexibleWidth = 1;
            CreateText(leftCol, "Status", 16, FontStyle.Bold).gameObject.AddComponent<LayoutElement>().preferredHeight = 24;
            var statusText = CreateText(leftCol, "Not connected", 14, FontStyle.Normal);
            statusText.alignment = TextAnchor.UpperLeft;
            var statusLE = statusText.gameObject.AddComponent<LayoutElement>();
            statusLE.preferredHeight = 180;
            statusLE.flexibleHeight = 0;

            CreateText(leftCol, "Log", 16, FontStyle.Bold).gameObject.AddComponent<LayoutElement>().preferredHeight = 24;
            var logText = CreateText(leftCol, "", 12, FontStyle.Normal);
            logText.alignment = TextAnchor.UpperLeft;
            logText.gameObject.AddComponent<LayoutElement>().flexibleHeight = 1;

            var rightCol = CreateVertical(bodyRow);
            rightCol.gameObject.AddComponent<LayoutElement>().flexibleWidth = 1;
            CreateText(rightCol, "Preview (HTTP JPEG)", 16, FontStyle.Bold).gameObject.AddComponent<LayoutElement>().preferredHeight = 24;
            var previewPanel = CreatePanel(rightCol, "PreviewPanel", Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            previewPanel.gameObject.AddComponent<LayoutElement>().flexibleHeight = 1;
            var previewImage = previewPanel.gameObject.AddComponent<RawImage>();
            previewImage.color = Color.black;

            startBtn.interactable = false;
            stopBtn.interactable = false;

            return new EgoDemoUiRefs
            {
                HostInput = hostInput,
                PortInput = portInput,
                StatusText = statusText,
                LogText = logText,
                PreviewImage = previewImage,
                ConnectButton = connectBtn,
                StartButton = startBtn,
                StopButton = stopBtn,
                RefreshButton = refreshBtn,
                ViewButtons = viewButtons,
            };
        }

        static RectTransform CreatePanel(Transform parent, string name, Vector2 anchorMin, Vector2 anchorMax, Vector2 offsetMin, Vector2 offsetMax)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image));
            go.transform.SetParent(parent, false);
            go.GetComponent<Image>().color = new Color(0.12f, 0.12f, 0.14f, 0.95f);
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = anchorMin;
            rt.anchorMax = anchorMax;
            rt.offsetMin = offsetMin;
            rt.offsetMax = offsetMax;
            return rt;
        }

        static RectTransform CreateHorizontal(Transform parent)
        {
            var go = new GameObject("Row", typeof(RectTransform), typeof(HorizontalLayoutGroup));
            go.transform.SetParent(parent, false);
            var h = go.GetComponent<HorizontalLayoutGroup>();
            h.spacing = 8;
            h.childControlHeight = true;
            h.childControlWidth = false;
            h.childForceExpandHeight = false;
            h.childForceExpandWidth = false;
            go.AddComponent<LayoutElement>().preferredHeight = 40;
            return go.GetComponent<RectTransform>();
        }

        static RectTransform CreateVertical(Transform parent)
        {
            var go = new GameObject("Column", typeof(RectTransform), typeof(VerticalLayoutGroup));
            go.transform.SetParent(parent, false);
            var v = go.GetComponent<VerticalLayoutGroup>();
            v.spacing = 6;
            v.childControlHeight = true;
            v.childControlWidth = true;
            v.childForceExpandHeight = false;
            v.childForceExpandWidth = true;
            return go.GetComponent<RectTransform>();
        }

        static Text CreateText(Transform parent, string text, int size, FontStyle style)
        {
            var go = new GameObject("Text", typeof(RectTransform), typeof(Text));
            go.transform.SetParent(parent, false);
            var t = go.GetComponent<Text>();
            t.text = text;
            t.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            t.fontSize = size;
            t.fontStyle = style;
            t.color = Color.white;
            t.horizontalOverflow = HorizontalWrapMode.Wrap;
            t.verticalOverflow = VerticalWrapMode.Overflow;
            return t;
        }

        static InputField CreateInput(Transform parent, string placeholder, float width)
        {
            var go = new GameObject("Input", typeof(RectTransform), typeof(Image), typeof(InputField));
            go.transform.SetParent(parent, false);
            go.GetComponent<Image>().color = new Color(0.2f, 0.2f, 0.22f);
            var le = go.AddComponent<LayoutElement>();
            le.preferredWidth = width;
            le.preferredHeight = 34;

            var textGo = new GameObject("Text", typeof(RectTransform), typeof(Text));
            textGo.transform.SetParent(go.transform, false);
            var text = textGo.GetComponent<Text>();
            text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.supportRichText = false;
            text.color = Color.white;
            text.fontSize = 14;
            var textRt = textGo.GetComponent<RectTransform>();
            textRt.anchorMin = Vector2.zero;
            textRt.anchorMax = Vector2.one;
            textRt.offsetMin = new Vector2(8, 4);
            textRt.offsetMax = new Vector2(-8, -4);

            var phGo = new GameObject("Placeholder", typeof(RectTransform), typeof(Text));
            phGo.transform.SetParent(go.transform, false);
            var ph = phGo.GetComponent<Text>();
            ph.font = text.font;
            ph.text = placeholder;
            ph.color = new Color(1, 1, 1, 0.35f);
            ph.fontSize = 14;
            var phRt = phGo.GetComponent<RectTransform>();
            phRt.anchorMin = Vector2.zero;
            phRt.anchorMax = Vector2.one;
            phRt.offsetMin = new Vector2(8, 4);
            phRt.offsetMax = new Vector2(-8, -4);

            var input = go.GetComponent<InputField>();
            input.textComponent = text;
            input.placeholder = ph;
            input.text = placeholder;
            return input;
        }

        static Button CreateButton(Transform parent, string label, float width)
        {
            var go = new GameObject(label, typeof(RectTransform), typeof(Image), typeof(Button));
            go.transform.SetParent(parent, false);
            go.GetComponent<Image>().color = new Color(0.24f, 0.45f, 0.85f);
            var le = go.AddComponent<LayoutElement>();
            le.preferredWidth = width;
            le.preferredHeight = 34;
            var btn = go.GetComponent<Button>();

            var text = CreateText(go.transform, label, 14, FontStyle.Normal);
            text.alignment = TextAnchor.MiddleCenter;
            var rt = text.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;

            return btn;
        }
    }
}
