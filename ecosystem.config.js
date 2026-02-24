module.exports = {
  apps: [
    {
      name: "tgbot",
      cwd: "/opt/tgbot",
      script: "bot.py",
      interpreter: "/root/miniconda3/envs/tgbot/bin/python",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      watch: false,

      min_uptime: "20s",
      max_restarts: 20,
      restart_delay: 3000,
      exp_backoff_restart_delay: 100,
      kill_timeout: 5000,
      max_memory_restart: "350M",

      out_file: "./logs/pm2-out.log",
      error_file: "./logs/pm2-error.log",
      merge_logs: true,
      time: true,

      env: {
        APP_ENV: "production",
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: "/opt"   // 关键：让 from tgbot.core... 可解析
      }
    }
  ]
};
