# Oracle VPS Auto Provisioner

Script que intenta crear una instancia gratuita de Oracle Cloud (Ampere A1) y avisa por email en cuanto lo consigue.

## Objetivo de la instancia

- Region fija: `eu-frankfurt-1` (Germany Central)
- Shape: `VM.Standard.A1.Flex` (Ampere ARM), 2 OCPUs y 12 GB de RAM
- Imagen: Canonical Ubuntu 24.04 Minimal aarch64 (se busca automaticamente la mas reciente)
- Prueba las 3 Availability Domains (AD-1, AD-2, AD-3) en cada vuelta
- Si Oracle responde "Out of capacity" no se marca error: se reintenta en la siguiente vuelta
- Si la instancia se crea, envia un email por SMTP de Gmail (`smtp.gmail.com:465`)

## Como funciona la espera

GitHub **no** cumple el `cron` cada 10 minutos: en la practica lanza una ejecucion cada varias horas.
Por eso cada ejecucion no se rinde a los 10 minutos, sino que sondea en bucle (1 intento por minuto,
rotando las 3 ADs) durante `MAX_RUNTIME_SECONDS` (5 h 30 min por defecto).

La regla `concurrency` del workflow impide que dos ejecuciones creen la instancia a la vez.

## Secrets necesarios (Settings -> Secrets and variables -> Actions)

| Secret | Contenido |
|---|---|
| `ORACLE_USER_OCID` | OCID del usuario |
| `ORACLE_TENANCY_OCID` | OCID de la tenancy |
| `ORACLE_COMPARTMENT_OCID` | OCID del compartment (si falta, se usa la tenancy) |
| `ORACLE_SUBNET_OCID` | OCID de la subnet regional |
| `ORACLE_API_FINGERPRINT` | Fingerprint de la API key |
| `ORACLE_API_KEY` | Contenido completo del archivo `.pem` |
| `EMAIL_FROM` | Cuenta de Gmail que envia |
| `EMAIL_PASSWORD` | Contrasena de aplicacion de Google (no la normal) |
| `EMAIL_TO` | Email de destino (por defecto `annaysergi@gmail.com`) |
| `SSH_PUBLIC_KEY` | Clave publica SSH (opcional) |
| `ORACLE_IMAGE_ID` | Imagen fija (opcional; si no existe, se busca sola) |

## Diagnostico

Cada respuesta de OCI se registra en el log con su `code`, `status` y mensaje, sin datos sensibles.
El log se sube como artefacto `provisioner-logs` en cada ejecucion.

## Ejecucion manual

Actions -> Oracle VPS Auto Provisioner -> Run workflow. El campo `max_runtime_seconds` permite
hacer una prueba corta (por ejemplo `180`) sin esperar las 5 h 30 min.

## Licencia

MIT
