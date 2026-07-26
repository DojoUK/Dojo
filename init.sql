-- Create dojo_user if it doesn't exist
CREATE USER IF NOT EXISTS 'dojo_user'@'%' IDENTIFIED BY 'dojo_password_123';
GRANT ALL PRIVILEGES ON dojo.* TO 'dojo_user'@'%';
FLUSH PRIVILEGES;
