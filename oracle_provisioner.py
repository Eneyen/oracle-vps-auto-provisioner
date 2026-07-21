#!/usr/bin/env python3
"""
Oracle VPS Auto Provisioner
Intenta continuamente crear una instancia de Oracle Cloud Free Tier y notifica por email cuando lo consigue.
"""

import os
import sys
import time
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import oci
from datetime import datetime
import logging

# Configurar logging
logging.basicConfig(
      level=logging.INFO,
      format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class OracleVPSProvisioner:
      def __init__(self):
                self.config = self.load_config()
                self.max_retries = 10
                self.retry_delay = 60  # segundos

      def load_config(self):
                """Carga la configuración desde variables de entorno o archivo"""
                config = {
                    'user_ocid': os.getenv('ORACLE_USER_OCID'),
                    'tenancy_ocid': os.getenv('ORACLE_TENANCY_OCID'),
                    'region': os.getenv('ORACLE_REGION', 'eu-frankfurt-1'),
                    'compartment_ocid': os.getenv('ORACLE_COMPARTMENT_OCID'),
                    'subnet_ocid': os.getenv('ORACLE_SUBNET_OCID'),
                    'api_key_path': os.getenv('ORACLE_API_KEY_PATH'),
                    'api_fingerprint': os.getenv('ORACLE_API_FINGERPRINT'),
                    'email_from': os.getenv('EMAIL_FROM'),
                    'email_password': os.getenv('EMAIL_PASSWORD'),
                    'email_to': os.getenv('EMAIL_TO'),
                    'instance_name': os.getenv('INSTANCE_NAME', f'vps-auto-{datetime.now().strftime("%Y%m%d-%H%M%S")}'),
                    'ocpu': int(os.getenv('OCPU', '2')),
                    'memory_gb': int(os.getenv('MEMORY_GB', '12')),
                }

        if not all([config['user_ocid'], config['tenancy_ocid'], config['compartment_ocid'], 
                                       config['subnet_ocid'], config['api_key_path'], config['api_fingerprint']]):
                                                     logger.error('Faltan variables de configuración de Oracle Cloud')
                                                     sys.exit(1)

        return config

    def create_oci_config(self):
              """Crea la configuración de OCI para la autenticación"""
              return {
                  "user": self.config['user_ocid'],
                  "key_file": self.config['api_key_path'],
                  "fingerprint": self.config['api_fingerprint'],
                  "tenancy": self.config['tenancy_ocid'],
                  "region": self.config['region']
              }

    def send_notification_email(self, instance_details):
              """Envía email de notificación cuando se crea la instancia"""
              try:
                            msg = MIMEMultipart()
                            msg['From'] = self.config['email_from']
                            msg['To'] = self.config['email_to']
                            msg['Subject'] = '✅ Oracle VPS creada exitosamente!'

            body = f"""
            ¡Éxito! Tu instancia de Oracle VPS ha sido creada.

            DETALLES DE LA INSTANCIA:
            - Nombre: {instance_details['display_name']}
            - ID: {instance_details['id']}
            - Estado: {instance_details['lifecycle_state']}
            - Region: {self.config['region']}
            - IP Pública: {instance_details.get('public_ip', 'En asignación...')}

            ESPECIFICACIONES:
            - OCPUs: {self.config['ocpu']}
            - Memoria: {self.config['memory_gb']}GB
            - Compartimento: {self.config['compartment_ocid']}

            Hora de creación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

            Tu VPS está lista para usar.
                        """

            msg.attach(MIMEText(body, 'plain'))

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                              server.login(self.config['email_from'], self.config['email_password'])
                              server.send_message(msg)

            logger.info(f'Email de notificación enviado a {self.config["email_to"]}')
            return True
except Exception as e:
            logger.error(f'Error enviando email: {e}')
            return False

    def try_create_instance(self):
              """Intenta crear la instancia de Oracle Cloud"""
              try:
                            oci_config = self.create_oci_config()
                            compute_client = oci.core.ComputeClient(oci_config)

            # Configuración de la instancia
                  instance_details = oci.core.models.LaunchInstanceDetails(
                                    display_name=self.config['instance_name'],
                                    compartment_id=self.config['compartment_ocid'],
                                    image_id=self.get_free_tier_image(),
                                    shape="VM.Standard.A1.Flex",
                                    subnet_id=self.config['subnet_ocid'],
                                    shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                                                          ocpus=self.config['ocpu'],
                                                          memory_in_gbs=self.config['memory_gb']
                                    ),
                                    metadata={
                                                          "ssh_authorized_keys": self.get_ssh_key()
                                    }
                  )

            response = compute_client.launch_instance(instance_details)
            instance = response.data

            logger.info(f'✅ Instancia creada exitosamente!')
            logger.info(f'Instance ID: {instance.id}')

            # Enviar email
            self.send_notification_email({
                              'display_name': instance.display_name,
                              'id': instance.id,
                              'lifecycle_state': instance.lifecycle_state,
                              'public_ip': getattr(instance, 'public_ip', 'Pendiente')
            })

            return True

except oci.exceptions.ServiceError as e:
            if e.status == 400 and 'Out of capacity' in str(e):
                              logger.warning(f'⚠️  Sin capacidad disponible. Reintentando en {self.retry_delay}s...')
                              return False
else:
                  logger.error(f'Error de OCI: {e}')
                  return False
except Exception as e:
            logger.error(f'Error inesperado: {e}')
            return False

    def get_free_tier_image(self):
              """Obtiene la imagen gratuita de Ubuntu para ARM Ampere A1"""
              # Esta función debería obtener dinámicamente la imagen más reciente
              # Por ahora retorna un placeholder - deberías actualizar con el ID real
        return os.getenv('ORACLE_IMAGE_ID', 'ocid1.image.oc1.eu-frankfurt-1.aaaaaaaxxxxxxx')

    def get_ssh_key(self):
              """Obtiene la clave SSH pública"""
              ssh_key_path = os.getenv('SSH_PUBLIC_KEY_PATH')
              if ssh_key_path and os.path.exists(ssh_key_path):
                            with open(ssh_key_path, 'r') as f:
                                              return f.read().strip()
                                      return ""

    def run_with_retries(self):
              """Ejecuta el script con reintentos"""
              for attempt in range(1, self.max_retries + 1):
                            logger.info(f'Intento {attempt}/{self.max_retries}...')

            if self.try_create_instance():
                              logger.info('✅ ¡Script completado exitosamente!')
                              return True

            if attempt < self.max_retries:
                              logger.info(f'Esperando {self.retry_delay} segundos antes del próximo intento...')
                              time.sleep(self.retry_delay)
else:
                  logger.error('❌ Se agotaron los intentos. No se pudo crear la instancia.')
                  return False

        return False

def main():
      provisioner = OracleVPSProvisioner()
      provisioner.run_with_retries()

if __name__ == '__main__':
      main()
