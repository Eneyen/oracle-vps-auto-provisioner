# Oracle VPS Auto Provisioner

Script automático que intenta continuamente crear una instancia de Oracle Cloud Free Tier y notifica por email cuando lo consigue.

## 🎯 Objetivo

Oracle Cloud ofrece 2 OCPUs y 12GB de RAM gratis, pero frecuentemente están fuera de stock. Este script intenta crear una instancia automáticamente de forma continua en GitHub Actions y te notifica por email cuando lo consigue.

## ⚙️ Requisitos

- Cuenta de Oracle Cloud Free Tier
- - Credenciales de API de Oracle Cloud (.pem y fingerprint)
  - - Cuenta de Gmail con contraseña de aplicación
    - - GitHub repository con Actions habilitadas
     
      - ## 🚀 Setup
     
      - ### 1. Configurar Oracle Cloud
     
      - 1. Navega a **Oracle Cloud Console** → **Perfil** → **Tokens y claves**
        2. 2. Crea una nueva API Key y descarga el archivo `.pem`
           3. 3. Copia tu fingerprint
              4. 4. Obtén estos OCIDs desde tu perfil:
                 5.    - User OCID
                       -    - Tenancy OCID
                            -    - Compartment OCID
                                 -    - Subnet OCID
                                  
                                      - ### 2. Configurar Gmail
                                  
                                      - 1. Activa verificación en dos pasos en tu cuenta Google
                                        2. 2. Genera una **contraseña de aplicación** (no tu contraseña normal)
                                           3. 3. Guarda esa contraseña
                                             
                                              4. ### 3. Configurar GitHub Secrets
                                             
                                              5. En tu repositorio GitHub:
                                              6. 1. Ve a **Settings** → **Secrets and variables** → **Actions**
                                                 2. 2. Añade estos secrets:
                                                    3.    - `ORACLE_USER_OCID`
                                                          -    - `ORACLE_TENANCY_OCID`
                                                               -    - `ORACLE_COMPARTMENT_OCID`
                                                                    -    - `ORACLE_SUBNET_OCID`
                                                                         -    - `ORACLE_API_FINGERPRINT`
                                                                              -    - `ORACLE_API_KEY` (contenido del archivo `.pem`)
                                                                                   -    - `ORACLE_IMAGE_ID` (ID de imagen gratuita de Ubuntu para Ampere A1)
                                                                                        -    - `EMAIL_FROM` (tu email de Gmail)
                                                                                             -    - `EMAIL_PASSWORD` (contraseña de aplicación Google)
                                                                                                  -    - `EMAIL_TO` (email destino de notificaciones)
                                                                                                       -    - `SSH_PUBLIC_KEY` (opcional, para acceso SSH)
                                                                                                        
                                                                                                            - ### 4. Configurar el Workflow
                                                                                                        
                                                                                                            - El workflow en `.github/workflows/provision.yml` se ejecutará automáticamente cada hora. Puedes modificar la frecuencia editando el `schedule`.
                                                                                                        
                                                                                                            - ## 📝 Configuración
                                                                                                        
                                                                                                            - Variables de entorno disponibles en `oracle_provisioner.py`:
                                                                                                        
                                                                                                            - - `OCPU`: Número de OCPUs (default: 2)
                                                                                                              - - `MEMORY_GB`: Memoria en GB (default: 12)
                                                                                                                - - `INSTANCE_NAME`: Nombre de la instancia
                                                                                                                  - - `ORACLE_REGION`: Región (default: eu-frankfurt-1)
                                                                                                                   
                                                                                                                    - ## 🔐 Seguridad
                                                                                                                   
                                                                                                                    - ⚠️ **IMPORTANTE:**
                                                                                                                    - - Nunca commits tu archivo `.pem` o credenciales
                                                                                                                      - - Usa GitHub Secrets para todas las credenciales
                                                                                                                        - - El `.pem` debe estar en un lugar seguro
                                                                                                                          - - La contraseña de Google es solo para que el script envíe emails
                                                                                                                           
                                                                                                                            - ## 🛠️ Troubleshooting
                                                                                                                           
                                                                                                                            - - **"Out of capacity"**: Oracle no tiene disponibilidad. El script seguirá intentando.
                                                                                                                              - - **"Invalid credentials"**: Verifica los OCIDs y API key en GitHub Secrets
                                                                                                                                - - **"Email not sent"**: Asegúrate de que la contraseña de aplicación Google es correcta
                                                                                                                                 
                                                                                                                                  - ## 📄 Licencia
                                                                                                                                 
                                                                                                                                  - MIT
                                                                                                                                 
                                                                                                                                  - ## 👤 Autor
                                                                                                                                 
                                                                                                                                  - Eneyen - Oracle VPS Auto Provisioner
