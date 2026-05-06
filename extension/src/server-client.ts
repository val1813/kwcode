import * as http from 'http';
import * as https from 'https';

/**
 * SSE client for connecting to kwcode server.
 * Thin client — no business logic, just HTTP + SSE transport.
 */
export class KwcodeServerClient {
    private baseUrl: string;

    constructor(baseUrl: string) {
        this.baseUrl = baseUrl.replace(/\/$/, '');
    }

    /**
     * Check if server is reachable.
     */
    async checkHealth(): Promise<boolean> {
        try {
            const resp = await this.fetch('/api/health');
            return resp.status === 'ok';
        } catch {
            return false;
        }
    }

    /**
     * Submit a task to the server.
     * Returns task_id for SSE streaming.
     */
    async submitTask(input: string, projectRoot?: string): Promise<string | null> {
        try {
            const body = JSON.stringify({
                input,
                project_root: projectRoot || '.',
            });
            const resp = await this.postJson('/api/task', body);
            return resp?.task_id || null;
        } catch {
            return null;
        }
    }

    /**
     * Stream SSE events for a task.
     * Calls onEvent for each event, onDone when stream ends.
     */
    streamEvents(
        taskId: string,
        onEvent: (event: Record<string, unknown>) => void,
        onDone: () => void,
        onError: (err: Error) => void,
    ): void {
        const url = `${this.baseUrl}/api/task/${taskId}/events`;
        const lib = url.startsWith('https') ? https : http;

        lib.get(url, (res) => {
            let buffer = '';

            res.on('data', (chunk: Buffer) => {
                buffer += chunk.toString();
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            onEvent(data);
                            if (data.event === 'task_completed' || data.event === 'task_error') {
                                onDone();
                                res.destroy();
                                return;
                            }
                        } catch {
                            // Skip malformed JSON
                        }
                    }
                }
            });

            res.on('end', onDone);
            res.on('error', onError);
        }).on('error', onError);
    }

    /**
     * Trigger rig.json refresh on the server.
     */
    async refreshRig(): Promise<void> {
        await this.postJson('/api/rig/refresh', '');
    }

    /**
     * Get file tree from server.
     */
    async getFiles(path: string = '.'): Promise<unknown> {
        return this.fetch(`/api/files?path=${encodeURIComponent(path)}`);
    }

    /**
     * Read a file from server.
     */
    async readFile(path: string): Promise<{ content: string; language: string } | null> {
        try {
            return await this.fetch(`/api/file?path=${encodeURIComponent(path)}`);
        } catch {
            return null;
        }
    }

    // ── Private helpers ──

    private fetch(path: string): Promise<any> {
        return new Promise((resolve, reject) => {
            const url = `${this.baseUrl}${path}`;
            const lib = url.startsWith('https') ? https : http;

            lib.get(url, (res) => {
                let data = '';
                res.on('data', (chunk) => { data += chunk; });
                res.on('end', () => {
                    try {
                        resolve(JSON.parse(data));
                    } catch {
                        reject(new Error(`Invalid JSON from ${path}`));
                    }
                });
                res.on('error', reject);
            }).on('error', reject);
        });
    }

    private postJson(path: string, body: string): Promise<any> {
        return new Promise((resolve, reject) => {
            const url = new URL(`${this.baseUrl}${path}`);
            const lib = url.protocol === 'https:' ? https : http;

            const options = {
                hostname: url.hostname,
                port: url.port,
                path: url.pathname,
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(body),
                },
            };

            const req = lib.request(options, (res) => {
                let data = '';
                res.on('data', (chunk) => { data += chunk; });
                res.on('end', () => {
                    try {
                        resolve(JSON.parse(data));
                    } catch {
                        resolve(null);
                    }
                });
                res.on('error', reject);
            });

            req.on('error', reject);
            req.write(body);
            req.end();
        });
    }
}
