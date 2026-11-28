// npm start: the reference on PORT, with its data in DB_FILE.
import { serve } from "@hono/node-server";
import { open } from "./core.ts";
import { build } from "./routes.ts";

const app = build(open(process.env.DB_FILE ?? "data.db"));
serve({ fetch: app.fetch, port: Number(process.env.PORT ?? 3000) });
