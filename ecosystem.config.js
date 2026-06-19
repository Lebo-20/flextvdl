module.exports = {
  apps: [
    {
      name: "flextv-bot",
      script: "/root/flextvdl/venv/bin/python3",
      args: "main.py",
      cwd: "/root/flextvdl",
      autorestart: true,
      watch: false,
      max_memory_restart: "1G",
      env: {
        NODE_ENV: "production",
      }
    }
  ]
};
