// Minimal project-local logger. Avoids bare console.log in committed code;
// swap for a real transport (file/OTel) later.

type Args = readonly unknown[];

export const log = {
  info: (...args: Args): void => console.info("[laura]", ...args),
  warn: (...args: Args): void => console.warn("[laura]", ...args),
  error: (...args: Args): void => console.error("[laura]", ...args),
};
