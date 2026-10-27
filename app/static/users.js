(() => {
  const byId = (id) => document.getElementById(id);
  const error = (id, err) => { byId(id).textContent = err?.message || String(err || ""); };
  const close = (id) => byId(id).classList.add("hidden");
  const open = (id) => byId(id).classList.remove("hidden");
  const action = (label, callback) => {
    const button = el("button", "action-button", label);
    button.type = "button";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await callback(); } catch (err) { window.alert(err.message); }
      finally { button.disabled = false; }
    });
    return button;
  };
  const listInto = (id, columns, rows) => byId(id).replaceChildren(makeTable(columns, rows));
  const formData = (form) => Object.fromEntries(new FormData(form));
  const authenticated = window.harnoqUser;
  const complexityLabels = { small: uiText("小"), medium: uiText("中"), large: uiText("大"), xlarge: uiText("超大") };
  const taskStatusLabels = { claimable: uiText("待认领"), claimed: uiText("已认领"), pre_reviewing: uiText("预审中"), ready_for_development: uiText("待开发"),
    in_progress: uiText("开发中"), ready_for_pr: uiText("待 PR"), completed: uiText("已完成"), needs_revision: uiText("待修订"), rejected_to_design: uiText("待修订") };

  window.harnoqAssignWork = async (task, path, reload) => {
    const users = await apiJson("/users");
    const overlay = el("div", "modal-overlay");
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    const modal = el("div", "modal");
    modal.appendChild(el("h2", "", uiText`分配 ${task.task_number || task.source_requirement_number || task.item_number}`));
    const form = el("form", "modal-form");
    const label = el("label", "", uiText("负责人"));
    const select = el("select");
    select.id = "task-owner-select";
    label.htmlFor = select.id;
    populateUserAssigneeSelect(select, task, users);
    form.append(label, select);
    const message = el("span", "form-error"); form.appendChild(message);
    const controls = el("div", "modal-actions");
    controls.appendChild(action(uiText("取消"), () => overlay.remove()));
    const submit = el("button", "action-button", uiText("保存")); submit.type = "submit";
    controls.appendChild(submit); form.appendChild(controls);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); submit.disabled = true;
      try {
        await apiJson(path, jsonOptions("POST", { assignee_number: select.value || null }));
        overlay.remove(); await reload();
      } catch (err) { message.textContent = err.message; }
      finally { submit.disabled = false; }
    });
    modal.appendChild(form); overlay.appendChild(modal); document.body.appendChild(overlay);
    select.focus();
    return false;
  };
  window.harnoqAssignTask = (task, reload) => window.harnoqAssignWork(task, `/tasks/${task.id}/assignee`, reload);

  document.addEventListener("DOMContentLoaded", () => {
    const login = byId("login-form");
    if (login) login.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = login.querySelector('button[type="submit"]'); submit.disabled = true;
      try {
        await apiJson("/auth/login", jsonOptions("POST", formData(login)));
        const next = new URLSearchParams(window.location.search).get("next");
        window.location.href = next && next.startsWith("/") && !next.startsWith("//") && !next.includes("\\") ? next : "/ui/projects";
      } catch (err) { error("login-error", err); }
      finally { submit.disabled = false; }
    });
    byId("auth-logout")?.addEventListener("click", async () => {
      await apiJson("/auth/logout", jsonOptions("POST")); window.location.href = "/ui/login";
    });

    if (byId("users-page")) {
      let editing = null;
      const form = byId("user-form");
      const showUser = (user = null) => {
        editing = user;
        form.reset();
        form.elements.username.disabled = Boolean(user);
        form.elements.password.required = !user;
        if (user) {
          for (const key of ["username", "display_name", "role", "is_active"]) form.elements[key].value = String(user[key]);
        }
        byId("user-modal-title").textContent = user ? uiText`编辑 ${user.display_name}` : uiText("添加用户");
        error("user-form-error", ""); open("user-modal");
      };
      const detail = async (user) => {
        byId("user-work-title").textContent = user.display_name;
        listInto("user-work-tasks", [uiText("任务"), uiText("标题"), uiText("状态"), uiText("复杂度")], user.tasks.map((task) => {
          const link = el("a", "", task.task_number); link.href = `/ui/tasks/${task.id}`;
          return [link, task.title, taskStatusLabels[task.status] || task.status, complexityLabels[task.complexity] || task.complexity];
        }));
        const activity = await apiJson(`/users/${user.user_number}/activity`);
        listInto("user-work-activity", [uiText("时间（UTC）"), uiText("动作"), uiText("对象")], activity.map((entry) => [entry.created_at, uiText(entry.action_label), entry.target_number]));
        open("user-work-detail");
      };
      const reload = async () => {
        const query = new URLSearchParams();
        for (const [id, key] of [["work-project", "project_id"], ["work-since", "since"], ["work-until", "until"]]) {
          if (byId(id).value) query.set(key, byId(id).value);
        }
        const result = await apiJson(`/users/workload?${query}`);
        listInto("users-table", [uiText("显示名称"), uiText("账号"), uiText("角色"), uiText("状态"), uiText("待办"), uiText("处理中"), uiText("当前完成"), uiText("期间完成"), uiText("复杂度分布"), uiText("操作")], result.users.map((user) => {
          const controls = el("div", "actions"); controls.appendChild(action(uiText("查看"), () => detail(user)));
          if (authenticated?.role === "admin" && !user.deleted) {
            controls.appendChild(action(uiText("编辑"), () => showUser(user)));
            controls.appendChild(action(uiText("删除"), async () => {
              if (!window.confirm(uiText`删除用户 ${user.display_name}？工作记录将保留。`)) return;
              await apiJson(`/users/${user.user_number}`, jsonOptions("DELETE")); await reload();
            }));
          }
          return [user.display_name, user.username, user.role === "admin" ? uiText("管理员") : uiText("成员"), user.deleted ? uiText("已删除") : user.is_active ? uiText("启用") : uiText("停用"),
            user.pending, user.active, user.completed, user.completed_in_period,
            Object.entries(user.complexity).map(([key, count]) => `${complexityLabels[key] || key}: ${count}`).join(" · ") || "—", controls];
        }));
        byId("users-unassigned").textContent = uiText`未关联账号的任务：${result.unassigned_tasks}。旧记录需要管理员明确分配后才归属到人。`;
        close("user-work-detail"); error("users-error", "");
      };
      byId("user-add")?.addEventListener("click", () => showUser());
      byId("user-cancel").addEventListener("click", () => close("user-modal"));
      byId("work-refresh").addEventListener("click", () => reload().catch((err) => error("users-error", err)));
      form.addEventListener("submit", async (event) => {
        event.preventDefault(); const data = formData(form); data.is_active = data.is_active === "true";
        const submit = form.querySelector('button[type="submit"]'); submit.disabled = true;
        if (!data.password && editing) delete data.password;
        try {
          await apiJson(editing ? `/users/${editing.user_number}` : "/users", jsonOptions(editing ? "PATCH" : "POST", data));
          form.reset(); close("user-modal"); await reload();
        } catch (err) { error("user-form-error", err); }
        finally { submit.disabled = false; }
      });
      apiJson("/projects").then((projects) => {
        projects.forEach((project) => {
          const option = el("option", "", `${project.name} · ${project.project_number}`);
          option.value = project.id; byId("work-project").appendChild(option);
        });
        return reload();
      }).catch((err) => error("users-error", err));
    }

    if (byId("account-page")) {
      const tokenForm = byId("token-form");
      const clearToken = () => {
        byId("token-value").value = ""; tokenForm.reset(); close("token-modal");
      };
      const reloadTokens = async () => {
        const tokens = await apiJson("/auth/tokens");
        listInto("token-list", [uiText("名称"), uiText("有效期（UTC）"), uiText("状态"), uiText("操作")], tokens.map((token) => [token.name, token.expires_at, token.revoked ? uiText("已撤销") : token.expired ? uiText("已过期") : uiText("可用"), token.revoked ? "—" : action(uiText("撤销"), async () => {
          if (!window.confirm(uiText`撤销凭证「${token.name}」？`)) return;
          await apiJson(`/auth/tokens/${token.credential_number}`, jsonOptions("DELETE")); await reloadTokens();
        })]));
      };
      byId("token-add").addEventListener("click", () => {
        open("token-form"); close("token-result"); error("token-form-error", ""); open("token-modal");
      });
      byId("token-cancel").addEventListener("click", clearToken);
      byId("token-done").addEventListener("click", clearToken);
      tokenForm.addEventListener("submit", async (event) => {
        event.preventDefault(); const data = formData(tokenForm); data.days = Number(data.days);
        const submit = tokenForm.querySelector('button[type="submit"]'); submit.disabled = true;
        try {
          const result = await apiJson("/auth/tokens", jsonOptions("POST", data));
          byId("token-value").value = result.access_token; close("token-form"); open("token-result"); await reloadTokens();
        } catch (err) { error("token-form-error", err); }
        finally { submit.disabled = false; }
      });
      byId("password-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await apiJson("/auth/password", jsonOptions("POST", formData(event.currentTarget)));
          window.location.href = "/ui/login";
        } catch (err) { error("account-error", err); }
      });
      reloadTokens().catch((err) => error("account-error", err));
    }
  });
})();
