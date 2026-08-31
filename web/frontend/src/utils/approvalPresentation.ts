function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  return typeof value === "string" ? value.trim() : "";
}

function commandPath(command: string): string {
  const match = command.match(/(?:-Path|-LiteralPath)\s+(?:"([^"]+)"|'([^']+)'|(\S+))/i);
  return match?.[1] ?? match?.[2] ?? match?.[3] ?? "代码仓库";
}

export function describeApprovalPurpose(
  toolName: string,
  args: Record<string, unknown>,
): string {
  const path = stringArg(args, "path");
  const command = stringArg(args, "command");

  if (toolName === "run_command") {
    if (/\b(?:Select-String|rg|grep)\b/i.test(command)) {
      return `hako 想在 ${commandPath(command)} 中搜索相关实现，确认代码位置后再决定下一步；这是一项只读检查。`;
    }
    if (/\b(?:pytest|unittest|test|mvnw?|gradlew?|npm|pnpm|yarn)\b/i.test(command)) {
      return "hako 想运行项目测试或构建，确认当前实现是否满足要求；命令可能生成测试缓存或构建产物。";
    }
    return "hako 想执行这条命令来取得下一步所需的运行结果；命令可能启动进程或改变工作区，因此需要你确认。";
  }
  if (["edit_file", "write_file"].includes(toolName)) {
    return `hako 准备修改 ${path || "工作区文件"}，把当前方案落到代码中；批准后修改会立即写入，取消不会自动回滚。`;
  }
  if (toolName === "read_file") {
    return `hako 想读取 ${path || "目标文件"} 以核对现有实现；这一步不会修改文件。`;
  }
  if (toolName === "list_dir") {
    return `hako 想查看 ${path || "工作区"} 的目录结构，以确认相关代码位置；这一步不会修改文件。`;
  }
  return "hako 需要执行这一步才能继续当前任务；请根据下方实际参数决定是否允许。";
}
