import { parentPort, workerData } from "node:worker_threads";
import { claimSession } from "./session-claim.mjs";

const { root, sessionId, workerId, leaseSec } = workerData;
parentPort.postMessage(claimSession(root, sessionId, workerId, { leaseSec }));
