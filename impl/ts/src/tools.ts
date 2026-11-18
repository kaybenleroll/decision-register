// Tool handlers (I1 scope). Filled in by the following commits.

export type JsonObject = Record<string, unknown>;

export const IMPLEMENTED: readonly string[] = [];

export interface ToolResult {
  text: string;
  isError: boolean;
}

export class App {
  /** Runs a tool. `null` means the tool name is unknown (a protocol error). */
  call(name: string, _args: unknown, _modelFacing: boolean): ToolResult | null {
    void name;
    return null;
  }
}
