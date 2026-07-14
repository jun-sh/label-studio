/* Client-side L1 skill derivation (mirrors backend/skill_derivation.py). */
(function (global) {
  const DEFAULT_SKILL_DERIVATION = {
    enabled: true,
    cycle_start_labels: ['reach'],
    skills: {
      approach_skill: {
        from_label: 'reach',
        until_label: 'pre_grasp',
        until_exclusive: true,
      },
      grasp_skill: {
        from_label: 'pre_grasp',
        until_label: 'lift',
        until_inclusive: true,
      },
      place_skill: {
        from_label: 'transport',
        until_label: 'release',
        until_inclusive: true,
      },
    },
  };

  const DEFAULT_SKILL_LABELS = [
    {
      id: 'approach_skill',
      subgoal_en: 'move close to the target box and prepare for grasping',
    },
    {
      id: 'grasp_skill',
      subgoal_en: 'pre-align, contact and lift the box stably',
    },
    {
      id: 'place_skill',
      subgoal_en: 'transport the box and place it on the conveyor belt',
    },
  ];

  function skillDerivationConfig(schema) {
    if (!schema) return null;
    const cfg = schema.skill_derivation;
    if (!cfg || !cfg.enabled) return null;
    const merged = {
      ...DEFAULT_SKILL_DERIVATION,
      ...cfg,
      skills: { ...DEFAULT_SKILL_DERIVATION.skills },
    };
    if (cfg.skills && typeof cfg.skills === 'object') {
      Object.entries(cfg.skills).forEach(([skillId, rule]) => {
        merged.skills[skillId] = { ...(merged.skills[skillId] || {}), ...rule };
      });
    }
    return merged;
  }

  function skillSubgoalText(skillId, schema) {
    const labels = (schema && schema.skill_labels) || DEFAULT_SKILL_LABELS;
    const lbl = labels.find((item) => item.id === skillId);
    if (!lbl) return null;
    return lbl.subgoal_en || lbl.subgoal_zh || null;
  }

  function sortedSegments(subtasks) {
    return [...(subtasks || [])]
      .filter((s) => s && s.label != null)
      .sort((a, b) => {
        const ds = Number(a.start) - Number(b.start);
        if (ds !== 0) return ds;
        return Number(a.end) - Number(b.end);
      });
  }

  function segmentsByLabel(segments) {
    const out = {};
    segments.forEach((seg) => {
      const label = String(seg.label || '');
      if (!out[label]) out[label] = [];
      out[label].push(seg);
    });
    return out;
  }

  function firstStart(segments, label) {
    const list = segmentsByLabel(segments)[label];
    if (!list || !list.length) return null;
    return Number(list[0].start);
  }

  function lastEnd(segments, label) {
    const list = segmentsByLabel(segments)[label];
    if (!list || !list.length) return null;
    return Math.max(...list.map((s) => Number(s.end)));
  }

  function splitSubtasksIntoCycles(subtasks, cycleStartLabels) {
    const segments = sortedSegments(subtasks);
    if (!segments.length) return [];

    const starts = new Set(cycleStartLabels || ['reach']);
    const cycles = [];
    let current = [];

    segments.forEach((seg) => {
      const label = String(seg.label || '');
      if (starts.has(label) && current.length) {
        const hasRelease = current.some((s) => s.label === 'release');
        if (hasRelease) {
          cycles.push(current);
          current = [seg];
          return;
        }
      }
      current.push(seg);
    });

    if (current.length) cycles.push(current);
    return cycles;
  }

  function deriveSkillSegment(cycleSegments, skillId, rule) {
    const fromLabel = String(rule.from_label || '');
    const untilLabel = String(rule.until_label || '');
    const untilExclusive = Boolean(rule.until_exclusive);
    const untilInclusive = rule.until_inclusive !== false;

    const start = firstStart(cycleSegments, fromLabel);
    if (start == null) return null;

    const untilStart = firstStart(cycleSegments, untilLabel);
    const untilEnd = lastEnd(cycleSegments, untilLabel);

    let end;
    if (untilExclusive) {
      if (untilStart == null) return null;
      end = untilStart;
    } else if (untilInclusive) {
      if (untilEnd == null) return null;
      end = untilEnd;
    } else {
      if (untilStart == null) return null;
      end = untilStart;
    }

    if (end <= start) return null;

    return {
      start,
      end,
      skill: skillId,
      source: 'auto',
    };
  }

  const CYCLE_STRUCTURE_EPSILON_SEC = 0.05;
  const CYCLE_USER_FIELDS = ['success', 'fail_reason', 'success_source', 'cycle_notes'];

  function cycleTimeBounds(cycleSegs) {
    if (!cycleSegs.length) return [0, 0];
    const starts = cycleSegs.map((s) => Number(s.start));
    const ends = cycleSegs.map((s) => Number(s.end));
    return [Math.min(...starts), Math.max(...ends)];
  }

  function cycleStructureSimilar(oldCycle, newCycle) {
    const oldStart = Number(oldCycle.start || 0);
    const oldEnd = Number(oldCycle.end || 0);
    const newStart = Number(newCycle.start || 0);
    const newEnd = Number(newCycle.end || 0);
    return (
      Math.abs(oldStart - newStart) <= CYCLE_STRUCTURE_EPSILON_SEC
      && Math.abs(oldEnd - newEnd) <= CYCLE_STRUCTURE_EPSILON_SEC
    );
  }

  function autoCycleDefaults(cycle) {
    const complete = Boolean(cycle.complete);
    return {
      ...cycle,
      success: complete,
      fail_reason: complete ? 'none' : 'incomplete',
      success_source: 'auto',
    };
  }

  function mergeSkillCycles(previous, derived) {
    const prevById = {};
    (previous || []).forEach((cycle) => {
      if (cycle.cycle_id != null) prevById[Number(cycle.cycle_id)] = cycle;
    });
    return (derived || []).map((cycle) => {
      const out = { ...cycle };
      const old = prevById[Number(cycle.cycle_id)];
      if (
        old
        && cycleStructureSimilar(old, cycle)
        && (
          old.success_source === 'manual'
          || (old.success_source == null && old.success != null)
        )
      ) {
        CYCLE_USER_FIELDS.forEach((key) => {
          if (Object.prototype.hasOwnProperty.call(old, key)) out[key] = old[key];
        });
        return out;
      }
      return autoCycleDefaults(out);
    });
  }

  function countSuccessfulCycles(cycles) {
    if (!cycles || !cycles.length) return null;
    if (!cycles.some((c) => c.success === true || c.success === false)) return null;
    return cycles.filter((c) => c.success === true).length;
  }

  function deriveSkillSegments(subtasks, schema, previousCycles) {
    const cfg = skillDerivationConfig(schema) || skillDerivationConfig({
      skill_derivation: DEFAULT_SKILL_DERIVATION,
    });
    if (!cfg) {
      return { skill_segments: [], cycles: [], warnings: [] };
    }

    const cyclesRaw = splitSubtasksIntoCycles(
      subtasks,
      cfg.cycle_start_labels || ['reach'],
    );

    const skillSegments = [];
    const cycles = [];
    const warnings = [];
    const skillRules = cfg.skills || {};

    cyclesRaw.forEach((cycleSegs, cycleId) => {
      const labelsPresent = new Set(cycleSegs.map((s) => String(s.label)));
      let cycleSkillCount = 0;

      Object.entries(skillRules).forEach(([skillId, rule]) => {
        const seg = deriveSkillSegment(cycleSegs, skillId, rule);
        if (!seg) {
          warnings.push(`cycle_${cycleId}: missing segment for ${skillId}`);
          return;
        }
        const subgoal = skillSubgoalText(skillId, schema);
        const enriched = { ...seg, cycle_id: cycleId };
        if (subgoal) enriched.subgoal = subgoal;
        skillSegments.push(enriched);
        cycleSkillCount += 1;
      });

      const complete = ['reach', 'pre_grasp', 'lift', 'transport', 'release'].every((lbl) =>
        labelsPresent.has(lbl),
      );
      const [startT, endT] = cycleTimeBounds(cycleSegs);
      cycles.push(
        autoCycleDefaults({
          cycle_id: cycleId,
          start: startT,
          end: endT,
          complete,
          labels: [...labelsPresent].sort(),
          skill_count: cycleSkillCount,
        }),
      );
      if (!complete) {
        warnings.push(`cycle_${cycleId}: incomplete phase pattern`);
      }
    });

    skillSegments.sort((a, b) => {
      const ds = a.start - b.start;
      return ds !== 0 ? ds : a.end - b.end;
    });

    const mergedCycles = mergeSkillCycles(previousCycles, cycles);

    return {
      skill_segments: skillSegments,
      cycles: mergedCycles,
      warnings,
    };
  }

  function skillDerivationMapLine(schema) {
    const cfg =
      skillDerivationConfig(schema) ||
      skillDerivationConfig({ skill_derivation: DEFAULT_SKILL_DERIVATION });
    if (!cfg || !cfg.skills) return '';

    const labels = (schema && schema.skill_labels) || DEFAULT_SKILL_LABELS;
    const orderedIds = labels.map((lbl) => lbl.id).filter((id) => cfg.skills[id]);
    const fallbackIds = Object.keys(cfg.skills);
    const skillIds = orderedIds.length ? orderedIds : fallbackIds;

    const parts = skillIds.map((skillId) => {
      const rule = cfg.skills[skillId];
      if (!rule) return null;
      const from = rule.from_label || '?';
      const until = rule.until_label || '?';
      return `${skillId} = ${from} → ${until}`;
    }).filter(Boolean);

    return parts.join(' · ');
  }

  global.SkillDerivation = {
    deriveSkillSegments,
    mergeSkillCycles,
    countSuccessfulCycles,
    skillDerivationConfig,
    skillDerivationMapLine,
    autoCycleDefaults,
  };
})(typeof window !== 'undefined' ? window : globalThis);
