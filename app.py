diff --git a/app.py b/app.py
index 22d4bc2..db4b295 100644
--- a/app.py
+++ b/app.py
@@ -5148,8 +5148,63 @@ def _config_cloudflare_meupainelnegocios():
         "env_prefix": "CLOUDFLARE_MEUPAINELNEGOCIOS",
     }
 
+
+def _config_cloudflare_mpaineldigital():
+    """Configuração isolada do domínio mpaineldigital.com.
+
+    Segue o mesmo padrão dos demais domínios dedicados: só entra na lista
+    quando estiver marcado como ativo e com as credenciais cadastradas na
+    Railway, sem interferir nos domínios já existentes (painelconectadobr.com
+    e meupainelnegocios.com continuam funcionando exatamente como antes).
+    """
+    ativo = valor_texto(os.environ.get(
+        "CLOUDFLARE_MPAINELDIGITAL_ATIVO", "0"
+    )).lower() not in {"0", "false", "nao", "não", "off", ""}
+
+    if not ativo:
+        return None
+
+    account_id = valor_texto(os.environ.get("CLOUDFLARE_MPAINELDIGITAL_ACCOUNT_ID", ""))
+    zone_id = valor_texto(os.environ.get("CLOUDFLARE_MPAINELDIGITAL_ZONE_ID", ""))
+    # Reaproveita o token do Painel Conectado por padrão, como já acontece
+    # com o Meu Painel Negócios, caso um token próprio não seja informado.
+    api_token = valor_texto(
+        os.environ.get("CLOUDFLARE_MPAINELDIGITAL_API_TOKEN", "")
+        or os.environ.get("CLOUDFLARE_PAINELCONECTADO_API_TOKEN", "")
+    )
+
+    # Sem conta, zona ou token o domínio não aparece no seletor, evitando
+    # publicação incompleta ou na zona errada.
+    if not account_id or not zone_id or not api_token:
+        return None
+
+    custom_domain = normalizar_dominio_cloudflare(
+        os.environ.get("CLOUDFLARE_MPAINELDIGITAL_ZONE_NAME", "mpaineldigital.com")
+    ) or "mpaineldigital.com"
+
+    return {
+        "account_id": account_id,
+        "api_token": api_token,
+        "subdomain": "",
+        "custom_domain": custom_domain,
+        "zone_id": zone_id,
+        "zone_name": custom_domain,
+        "ativo": True,
+        "publish_mode": valor_texto(os.environ.get(
+            "CLOUDFLARE_MPAINELDIGITAL_PUBLISH_MODE",
+            "custom_strict"
+        )).lower(),
+        "rotulo": "M Painel Digital",
+        "env_prefix": "CLOUDFLARE_MPAINELDIGITAL",
+    }
+
+
 def listar_configs_cloudflare():
-    dominios_permitidos = {"painelconectadobr.com", "meupainelnegocios.com"}
+    dominios_permitidos = {
+        "painelconectadobr.com",
+        "meupainelnegocios.com",
+        "mpaineldigital.com",
+    }
     bruto = valor_texto(os.environ.get("CLOUDFLARE_SITES_CONFIG", ""))
     configs = []
 
@@ -5183,6 +5238,7 @@ def listar_configs_cloudflare():
     for config_dedicada in [
         _config_cloudflare_painelconectado(),
         _config_cloudflare_meupainelnegocios(),
+        _config_cloudflare_mpaineldigital(),
     ]:
         if not config_dedicada:
             continue