const OBJECTS = {
  Image: {
    type: "Image",
    settings: {
      strokeWidth: {
        titleKey: "labeling_config.tag_settings.stroke_width",
        type: Number,
        param: ($obj, value) => $obj.$controls.forEach(($control) => $control.setAttribute("strokeWidth", value)),
        value: ($obj) => $obj.$controls[0]?.getAttribute("strokeWidth") ?? 1,
      },
      zoom: {
        titleKey: "labeling_config.tag_settings.zoom",
        type: Boolean,
        param: "zoom",
      },
      zoomControl: {
        titleKey: "labeling_config.tag_settings.zoom_control",
        type: Boolean,
        param: "zoomControl",
      },
      rotateControl: {
        titleKey: "labeling_config.tag_settings.rotate_control",
        type: Boolean,
        param: "rotateControl",
      },
    },
  },
  Text: {
    type: "Text",
    settings: {
      granularity: {
        titleKey: "labeling_config.tag_settings.granularity",
        type: Boolean,
        param: ($obj, value) =>
          value ? $obj.setAttribute("granularity", "word") : $obj.removeAttribute("granularity"),
        value: ($obj) => $obj.getAttribute("granularity") === "word",
        when: ($obj) => $obj.$controls.filter((c) => c.tagName.endsWith("Labels")).length > 0,
      },
    },
  },
  HyperText: {
    type: "HyperText",
  },
  Audio: {
    type: "Audio",
  },
  AudioPlus: {
    type: "Audio",
  },
  List: {
    type: "List",
  },
  Paragraphs: {
    type: "Paragraphs",
  },
  Table: {
    type: "Table",
  },
  TimeSeries: {
    type: "TimeSeries",
  },
  Video: {
    type: "Video",
  },
};

const Labels = {
  type: "Labels",
  settings: {
    placeLabelsLeft: {
      titleKey: "labeling_config.tag_settings.display_labels",
      type: ["bottom", "left", "right", "top"],
      control: true,
      when: ($tag) => $tag.$object.tagName !== "Video",
      param: ($control, value) => {
        let $container = $control.parentNode;
        let $labels = $control;

        if ($container.firstChild?.tagName?.toUpperCase() === "FILTER") {
          $labels = $container;
          $container = $labels.parentNode;
        }
        const $obj = $control.$object;
        const inline = ["top", "bottom"].includes(value);
        const reversed = ["top", "left"].includes(value);
        const direction = (inline ? "column" : "row") + (reversed ? "-reverse" : "");
        const alreadyApplied = $container.getAttribute("style")?.includes("flex");

        if (!alreadyApplied) {
          $container = $obj.ownerDocument.createElement("View");
          $labels.parentNode.insertBefore($container, $obj);
          $container.appendChild($obj);
          $container.appendChild($labels);
        }
        $control.setAttribute("showInline", JSON.stringify(inline));
        $container.setAttribute("style", `display:flex;align-items:start;gap:8px;flex-direction:${direction}`);
      },
      value: ($control) => {
        let $container = $control.parentNode;

        if ($container.firstChild?.tagName?.toUpperCase() === "FILTER") {
          $container = $container.parentNode;
        }
        const style = $container.getAttribute("style");
        const direction = style?.match(/direction:(row|column)(-reverse)?/);

        if (!direction) {
          const position = $control.compareDocumentPosition($control.$object);

          return position & Node.DOCUMENT_POSITION_FOLLOWING ? "top" : "bottom";
        }
        if (direction[1] === "column") return direction[2] ? "top" : "bottom";
        return direction[2] ? "left" : "right";
      },
    },
    filter: {
      titleKey: "labeling_config.tag_settings.filter",
      type: Boolean,
      control: true,
      param: ($obj, value) => {
        if (value) {
          const $filter = $obj.ownerDocument.createElement("Filter");
          const $container = $obj.ownerDocument.createElement("View");

          $filter.setAttribute("toName", $obj.getAttribute("name"));
          $filter.setAttribute("minlength", 0);
          $filter.setAttribute("name", "filter"); // @todo should be unique
          $obj.parentNode.insertBefore($container, $obj);
          $container.appendChild($filter);
          $container.appendChild($obj);
        } else {
          const $filter = $obj.previousElementSibling;

          if ($filter.tagName.toUpperCase() === "FILTER") {
            const $container = $obj.parentNode;

            $container.parentNode.insertBefore($obj, $container);
            $container.parentNode.removeChild($container);
          }
        }
      },
      value: ($control) => $control.previousElementSibling?.tagName.toUpperCase() === "FILTER",
    },
  },
};

const CONTROLS = {
  Labels,
  RectangleLabels: Labels,
};

const TAGS = { ...OBJECTS, ...CONTROLS };

export { OBJECTS, CONTROLS, TAGS };
