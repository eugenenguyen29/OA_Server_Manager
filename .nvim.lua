local lspconfig = require("lspconfig")

lspconfig.ruff.setup({
  root_dir = lspconfig.util.root_pattern("pyproject.toml", "ruff.toml", ".git"),
})

lspconfig.pylsp.setup({
  root_dir = lspconfig.util.root_pattern("pyproject.toml", ".git"),
  settings = {
    pylsp = {
      plugins = {
        pycodestyle = { enabled = false },
        pyflakes = { enabled = false },
        mccabe = { enabled = false },
        autopep8 = { enabled = false },
        yapf = { enabled = false },
      },
    },
  },
})
