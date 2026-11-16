export function isNumericGroupTarget(target) {
  return /^\d+$/.test(String(target || "").trim());
}

export function parseManualQQTarget(target) {
  const value = String(target || "").trim();
  if (!value) return null;

  let groupId = "";
  let platformId = "";
  let session = "";
  if (isNumericGroupTarget(value)) {
    groupId = value;
  } else {
    const firstSeparator = value.indexOf(":");
    const secondSeparator = firstSeparator >= 0
      ? value.indexOf(":", firstSeparator + 1)
      : -1;
    platformId = firstSeparator > 0 ? value.slice(0, firstSeparator).trim() : "";
    const messageType = secondSeparator > firstSeparator
      ? value.slice(firstSeparator + 1, secondSeparator).trim()
      : "";
    groupId = secondSeparator >= 0 ? value.slice(secondSeparator + 1).trim() : "";
    if (!platformId || messageType !== "GroupMessage" || !groupId) return null;
    session = `${platformId}:GroupMessage:${groupId}`;
  }

  return {
    target: session || groupId,
    group_id: groupId,
    platform_id: platformId,
    session,
    group: {
      group_id: groupId,
      group_name: session ? `QQ 群 ${groupId}` : `群 ${groupId}`,
      avatar: groupId && isNumericGroupTarget(groupId)
        ? `https://p.qlogo.cn/gh/${groupId}/${groupId}/640`
        : "",
      member_count: 0,
      max_member_count: 0,
      source: "configured",
      platform_id: platformId,
      session,
    },
  };
}

export function mergeQQTargetValue(targets, candidate) {
  const current = [];
  for (const rawTarget of Array.isArray(targets) ? targets : []) {
    const target = String(rawTarget || "").trim();
    if (target && !current.includes(target)) current.push(target);
  }

  const nextInfo = parseManualQQTarget(candidate);
  if (!nextInfo) return current;

  if (nextInfo.session) {
    const exactIndex = current.findIndex(
      (target) => parseManualQQTarget(target)?.session === nextInfo.session,
    );
    if (exactIndex >= 0) return current;
    const numericIndex = current.findIndex((target) => {
      const info = parseManualQQTarget(target);
      return info && !info.session && info.group_id === nextInfo.group_id;
    });
    if (numericIndex >= 0) {
      current[numericIndex] = nextInfo.target;
      return current;
    }
    current.push(nextInfo.target);
    return current;
  }

  const groupAlreadyPresent = current.some(
    (target) => parseManualQQTarget(target)?.group_id === nextInfo.group_id,
  );
  if (!groupAlreadyPresent) current.push(nextInfo.target);
  return current;
}
