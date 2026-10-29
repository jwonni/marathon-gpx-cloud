variable "my_ip_cidr" {
  description = "SSH·앱에 접속할 내 공인 IP (예: 1.2.3.4/32). 값은 my.auto.tfvars 에 둔다 (git 제외)"
  type        = string

  validation {
    condition     = can(cidrhost(var.my_ip_cidr, 0)) && endswith(var.my_ip_cidr, "/32")
    error_message = "IP 하나만 허용하도록 /32 로 끝나야 합니다. 0.0.0.0/0 같은 넓은 대역은 거부합니다."
  }
}
